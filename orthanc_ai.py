import os
import pydicom
import glob
import json
import hashlib
import re
import glob
import orthanc
import importlib.util, sys
import traceback
from pydicom import dcmread
from io import BytesIO
import threading

# In order to allow tools loading from inside modules, we add the "oai_modules" directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), "oai_modules"))
from tools import md5_file, clean_json, dir_public_attributes, flatten, push_files_to

## ABSOLUTE path for Orthanc AI
config_path = __file__.replace(".py",".json")

### Internal configuration
mandatory_parameters = ["ModuleLoadingHeuristic","AutoRemove","AutoReloadEach"]
mandatory_module_parameters = ["TriggerLevel","ClassName","CallingAET","DestinationName"]
authorized_triggers = ["Patient","Series","Study"]
list_filters = ["AccessionNumber","PatientName","PatientID","StudyDescription","SeriesDescription","ImageType",
                "InstitutionName", "InstitutionalDepartmentName", "Manufacturer", "ManufacturerModelName",
                "Modality", "OperatorsName", "PerformingPhysicianName", "ProtocolName", "StudyID"]


class OrthancAI():
    def __init__(self, config_path):
        # Initialisation on OrthancAI, should be called with the general configuration path
        self.config_path = config_path
        self.root_folder = os.path.dirname(os.path.realpath(config_path))
        self.main_config_loaded = False
        self.main_config_md5 = ""
        self.main_config = None
        self.modules_list = {}
        self.Timer = None
        self.LockTimer = True
        try:
            self.update_architecture() # Main subroutine for config loading and modules loading
        except Exception as e:
            orthanc.LogWarning("Error during loading config : " + str(e))
            print(traceback.format_exc())

    # Subroutine for auto-reloading modules
    def start_timer(self):
        if self.Timer is None:
            self.Timer = threading.Timer(self.main_config["AutoReloadEach"], self.perform_timer)
            self.Timer.start()
    def perform_timer(self):
        if not self.LockTimer:
            self.LockTimer = True
            self.Timer = None
            self.update_architecture()
            self.Timer = threading.Timer(self.main_config["AutoReloadEach"], self.perform_timer)
            self.LockTimer = False
            self.Timer.start()
    def stop_timer(self):
        if self.Timer is None:
            self.Timer.cancel()
            self.Timer = None

    def module_crawler(self):
        # get list of present modules according to heuristic in configuration file
        list_present_modules = glob.glob(os.path.join(self.root_folder,self.main_config["ModuleLoadingHeuristic"]))

        # For each module, we check if it needs loading
        for module_path in list_present_modules:
            module_id = os.path.basename(module_path).replace(".py","")
            try:
                if module_id in self.modules_list.keys():
                    # if the module has already been loaded, we check if there is any update necessary
                    self.check_module_update(module_id)
                else:
                    # first time we encounter this module: we load it
                    self.module_load(module_id, module_path)
            except Exception as e:
                orthanc.LogWarning("Error during loading module `" + module_id + "` : " + str(e))
                print(traceback.format_exc())

    def check_module_update(self, module_id):
        # Module is already loaded: we can call the update subroutine of OrthancAIModule class
        self.modules_list[module_id].check_module_update()
        # Garbage collection
        if not self.modules_list[module_id]: del self.modules_list[module_id]

    def check_mandatory_parameters(self, list_parameters, config=None):
        # check if all mandatory parameters in the main config are set
        if config is None:
            config = self.main_config
        for p in list_parameters:
            if p not in config.keys():
                raise Exception("Please specify mandatory `" + p + "` parameter for OrthancAI")

    def module_load(self, module_id, module_path):
        # load module (only if it is not already loaded)
        if module_id in self.modules_list.keys():
            raise Exception("Cannot load module before unloading")
        self.modules_list[module_id] = OrthancAIModule(module_id, module_path)
        # Garbage collection
        self.module_gc()

    def module_gc(self):
        # Garbage collector for unloaded modules
        for m in list(self.modules_list.keys()):
            if not self.modules_list[m]:
                del self.modules_list[m]

    def update_architecture(self):
        # main surboutine for refreshing the whole architecture
        # first we check the md5sum of main config file to see if it is changed
        config_md5 = md5_file(self.config_path)

        # config file loading if modified
        if config_md5 != self.main_config_md5:
            temporary_config = clean_json(self.config_path)
            self.check_mandatory_parameters(mandatory_parameters, temporary_config)
            self.main_config = temporary_config
            self.main_config_md5 = config_md5

        # load or reload modules
        self.module_crawler()

    def callback(self, changeType, level, resourceId):
        # main callback function called by orthanc API when events are triggered
        try:
            self.safe_callback(changeType, level, resourceId) # encapsulated into a try/except for safety
        except Exception as e:
            orthanc.LogWarning("Error during loading callback : " + str(e))
            print(traceback.format_exc())

    def safe_callback(self, changeType, level, resourceId):
        # the callback will be activated successively on stable series, studies, patients
        changeMode = ""
        if changeType == orthanc.ChangeType.STABLE_PATIENT:
            changeType = "Patient"
            instances = resourceId
        elif changeType == orthanc.ChangeType.STABLE_STUDY:
            changeType = "Study"
            instances = [resourceId]
        elif changeType == orthanc.ChangeType.STABLE_SERIES:
            changeType = "Series"
            instances = [[resourceId]]
        elif changeType == orthanc.ChangeType.ORTHANC_STARTED:
            self.start_timer()
            self.LockTimer = False
            return
        elif changeType == orthanc.ChangeType.ORTHANC_STOPPED:
            self.LockTimer = True
            self.stop_timer()
            return
        else:
            return # other event, not supported

        self.LockTimer = True # prevent any module loading during callback
        print("Callback `" + changeType + "` with instance : " + resourceId)

        # update the OrthancAI architecture, if needed
        self.update_architecture()

        # we get all instances pushed onto orthanc - we split external and internal (from plugin)
        numinstances = 0
        externalInstances = []
        internalInstances = []
        if type(instances) is str:
            instances = json.loads(orthanc.RestApiGet("/patients/"+instances))["Studies"]
        for st in range(len(instances)):
            externalInstances.append([])
            internalInstances.append([])
            if type(instances[st]) is str:
                instances[st] = json.loads(orthanc.RestApiGet("/studies/"+instances[st]))["Series"]
            for se in range(len(instances[st])):
                externalInstances[st].append([])
                internalInstances[st].append([])
                if type(instances[st][se]) is str:
                    instances[st][se] = json.loads(orthanc.RestApiGet("/series/"+instances[st][se]))["Instances"]
                    for ins in range(len(instances[st][se])):
                        # check if instance is not internal (shouldn't be treated)
                        instanceMetadata = json.loads(orthanc.RestApiGet("/instances/"+ \
                                                      instances[st][se][ins]+"/metadata?expand"))
                        if instanceMetadata["Origin"] != "Plugins":
                            externalInstances[st][se].append(instances[st][se][ins])
                        else:
                            internalInstances[st][se].append(instances[st][se][ins])

        if len(flatten(internalInstances)) > 0:
            # internal file send by OrthancAI: the files will be deleted
            if changeType == "Patient":
                if self.main_config["AutoRemove"]:
                    self.cleanup_instances(internalInstances) # cleanup already used resources
        else:
            # we get metadata (which contains the sender AET) on the first instance
            metadata = json.loads(orthanc.RestApiGet("/instances/"+externalInstances[0][0][0]+"/metadata?expand"))
            # external file send : dispatch to different modules
            # we check if there is any module to call
            moduleToCall = False
            for module in self.modules_list.values():
                if module.config["TriggerLevel"] == changeType and metadata["CalledAET"] == module.config["CallingAET"]:
                    moduleToCall = True
            if moduleToCall:
                # we collect all files in memory
                allfiles = []
                for st in range(len(externalInstances)):
                    allfiles.append([])
                    for se in range(len(externalInstances[st])):
                        allfiles[st].append([])
                        for instanceId in externalInstances[st][se]:
                            # extract each dicom file in pydicom format
                            f = orthanc.GetDicomForInstance(instanceId)
                            dc = dcmread(BytesIO(f))
                            allfiles[st][se].append(dc)
                # then, we check each module compatible with the trigger type
                for module in self.modules_list.values():
                    if module.config["TriggerLevel"] == changeType and metadata["CalledAET"] == module.config["CallingAET"]:
                        files = []
                        for st in range(len(allfiles)): # create recursive array of files
                            files.append([])
                            for se in range(len(allfiles[st])):
                                files[st].append([])
                                for im in allfiles[st][se]:
                                    if module.apply_filters(im):
                                        # for each file and module, we check if it matches the Positive and Negative filters
                                        files[st][se].append(im)
                        # clean up empty arrays if necessary
                        for st in reversed(range(len(files))):
                            for se in reversed(range(len(files[st]))):
                                if len(files[st][se]) == 0: del files[st][se]
                            if len(files[st]) == 0: del files[st]
                        if len(files) > 0:
                            # format the files array in the correct shape
                            if changeType == "Study": files = files[0]
                            if changeType == "Series": files = files[0][0]
                            try:
                                # send the filtered files to the module
                                orthanc.LogWarning("Calling `" + module.module_id + "` at level : " + changeType + \
                                                " with " + str(len(files)) + " files")
                                processed_files = module.process(files, metadata["RemoteAET"])
                                if processed_files and processed_files is not None:
                                    # if the module has returned files, we push them to DICOM server
                                    self.push_files(processed_files, module.config["DestinationName"])
                            except Exception as e:
                                orthanc.LogWarning("Error during module `" + module.module_id + "` processing : " + str(e))
                                print(traceback.format_exc())

            # StablePatient is always the last fired event : we clean up all instances
            if changeType == "Patient" and self.main_config["AutoRemove"]:
                self.cleanup_instances(externalInstances)
        self.LockTimer = False # free auto-reloading

    def cleanup_instances(self, instances):
        # delete list of instanceId using orthanc API
        if type(instances) is not list:
            instances = [instances]
        instances = flatten(instances)
        if len(instances) == 0:
            return
        postString = json.dumps({"Resources":instances})
        orthanc.RestApiPost("/tools/bulk-delete", postString)

    def push_files(self, files, destination):
        # push dicom files to DICOM destination
        push_files_to(files, destination)

class OrthancAIModule():
    # Main wrapper for each OrthancAI module
    def __init__(self, module_id, module_path):
        # initialize the module. MD5 values will be used to monitor changes
        self.loaded = False
        self.module_id = module_id
        self.module_path = module_path
        self.module_md5 = None
        self.config_path = module_path.replace(".py",".json")
        self.config_md5 = None
        self.config = {}
        # variables for storing module data
        self.module_lib = None
        self.module_class = None
        self.module_instance = None
        # load config and module
        self.load_config()

    def check_mandatory_parameters(self, list_parameters):
        # subroutine used for checking the presence of mandatory parameters in module config
        for p in list_parameters:
            if p not in self.config.keys():
                raise Exception("Please specify mandatory `" + p + "` parameter for " + self.module_id + " module")

    def load_config(self):
        # load config.json
        if not os.path.exists(self.module_path):
            raise Exception("Cannot load find ``" + self.module_path + "``")
        if not os.path.exists(self.config_path):
            raise Exception("Cannot load find ``" + self.config_path + "``")
        self.config = clean_json(self.config_path)
        self.config_md5 = md5_file(self.config_path)
        # Check parameters validity
        self.check_mandatory_parameters(mandatory_module_parameters)
        if self.config["TriggerLevel"] not in authorized_triggers:
            raise Exception("Invalid `TriggerLevel` parameter for " + self.module_id + " module")
        # load module
        self.load_module()

    def load_module(self):
        if self.loaded:
            raise Exception("Please unload module before loading it")
        # complex module loading for avoiding deprecation...
        self.module_md5 = md5_file(self.module_path)
        self.module_spec = importlib.util.spec_from_file_location(self.module_id, self.module_path)
        self.module_lib = importlib.util.module_from_spec(self.module_spec)
        sys.modules[self.module_id] = self.module_lib
        self.module_spec.loader.exec_module(self.module_lib)
        # we get the main class using parameter in the config file
        self.module_class = getattr(self.module_lib, self.config["ClassName"])
        # we call the __init__ subroutine of the loaded module
        self.module_instance = self.module_class(self.config)
        orthanc.LogWarning("Loaded module ``" + self.module_id + "``")
        self.loaded = True

    def check_module_update(self):
        # subroutine called to check if there is config or python update
        if self.config_md5 != md5_file(self.config_path):
            # config change : we reload config and module
            del self.module_lib, self.module_class, self.module_instance, self.config
            self.module_md5 = None
            self.config_md5 = None
            self.loaded = False
            orthanc.LogWarning("Reloading config and module `" + self.module_id + "`...")
            self.load_config()
        elif self.module_md5 != md5_file(self.module_path):
            # python change : we reload only the module
            del self.module_lib, self.module_class, self.module_instance
            self.module_md5 = None
            self.loaded = False
            orthanc.LogWarning("Reloading module `" + self.module_id + "`...")
            self.load_module()

    def apply_filters(self, file):
        # Subroutine called by the main OrthancAI callback to check if each file may be sent to the module
        # First we have a look at the positive filters
        if "Filters" in self.config.keys() and type(self.config["Filters"]) == dict:
            for positiveFilter in list_filters:
                if positiveFilter in self.config["Filters"].keys():
                    foundPositive = False
                    if hasattr(file,positiveFilter):
                        attribute = str(getattr(file,positiveFilter))
                        for filter in self.config["Filters"][positiveFilter]:
                            if re.search(filter, attribute) is not None:
                                foundPositive = True
                                break
                        if not foundPositive:
                            return False
                            # the attribute did not match with any positive filter: we don't use this file
                    else:
                        return False
                        # The file does not have the required attribute : we don't use it
        # Then we check for negative filters
        if "NegativeFilters" in self.config.keys() and type(self.config["NegativeFilters"]) == dict:
            for negativeFilter in list_filters:
                if negativeFilter in self.config["NegativeFilters"].keys():
                    if hasattr(file,negativeFilter):
                        attribute = str(getattr(file,negativeFilter))
                        for filter in self.config["NegativeFilters"][negativeFilter]:
                            # If there is ANY match with ANY Negative Filter, we dump the file
                            if re.search(filter, attribute) is not None:
                                return False
        return True

    def process(self, files, remote_aet):
        # Calling the module process subroutine
        if self.module_instance is not None:
            return self.module_instance.process(files, remote_aet)
        else:
            return []

    def __bool__(self):
        return self.loaded


# Creation of OrthancAI
oia = OrthancAI(config_path)
# registering triggers
orthanc.RegisterOnChangeCallback(oia.callback)