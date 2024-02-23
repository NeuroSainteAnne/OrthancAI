import os
import pydicom
import glob
import json
import hashlib
import re
import glob
import orthanc
import imp
import traceback
from pydicom import dcmread
from io import BytesIO

## ABSOLUTE configuration path for Orthanc AI
config_path = "/etc/orthanc/orthanc_ai.json"

### Internal configuration
mandatory_parameters = ["ModuleLoadingHeuristic","ExportDestination"]
mandatory_module_parameters = ["TriggerLevel","ClassName","CallingAET"]
authorized_triggers = ["Patient","Series","Study"]
list_filters = ["AccessionNumber","PatientName","PatientID","StudyDescription","SeriesDescription","ImageType",
                "InstitutionName", "InstitutionalDepartmentName", "Manufacturer", "ManufacturerModelName",
                "Modality", "OperatorsName", "PerformingPhysicianName", "ProtocolName", "StudyID"]

def md5_file(filepath):
    with open(filepath, "rb") as f:
        filedata = f.read()
    return hashlib.md5(filedata).hexdigest()

def clean_json(filepath):
    with open(filepath) as cf_file:
        # remove comments
        cf_data = ''.join(re.sub(r'\/\/.*', '', line) for line in cf_file)
        # store config file
        try:
            return json.loads(cf_data)
        except Exception as e:
            raise Exception("Error during reading JSON `" + filepath + "` : " + str(e))

def dir_public_attributes(obj):
    return [x for x in dir(obj) if not x.startswith('__')]

class OrthancAI():
    def __init__(self, config_path):
        self.config_path = config_path
        self.root_folder = os.path.dirname(os.path.realpath(config_path))
        self.main_config_loaded = False
        self.main_config_md5 = ""
        self.main_config = None
        self.modules_list = {}
        try:
            self.main_config_read()
        except Exception as e:
            orthanc.LogWarning("Error during loading config : " + str(e))
            print(traceback.format_exc())

    def module_crawler(self):
        # get list of present modules according to heuristic
        list_present_modules = glob.glob(os.path.join(self.root_folder,self.main_config["ModuleLoadingHeuristic"]))
        for module_path in list_present_modules:
            module_id = os.path.basename(module_path).replace(".py","")
            try:
                if module_id in self.modules_list.keys():
                        self.check_module_update(module_id)
                else:
                    self.module_load(module_id, module_path)
            except:
                orthanc.LogWarning("Error during loading module `" + module_id + "` : " + str(e))
                print(traceback.format_exc())

    def check_module_update(self, module_id):
        self.modules_list[module_id].check_module_update()
        if not self.modules_list[module_id]: del self.modules_list[module_id]


    def check_mandatory_parameters(self, list_parameters, config=None):
        if config is None:
            config = self.main_config
        for p in list_parameters:
            if p not in config.keys():
                raise Exception("Please specify mandatory `" + p + "` parameter for OrthancAI")

    def module_load(self, module_id, module_path):
        if module_id in self.modules_list.keys():
            raise Exception("Cannot load module before unloading")
        self.modules_list[module_id] = OrthancAIModule(module_id, module_path)
        self.module_gc()

    def module_gc(self):
        # Garbage collector for unloaded modules
        for m in list(self.modules_list.keys()):
            if not self.modules_list[m]:
                del self.modules_list[m]

    def main_config_read(self):
        # first we check the md5sum of config file to see if it is changed
        config_md5 = md5_file(self.config_path)

        # config file loading
        if config_md5 != self.main_config_md5:
            temporary_config = clean_json(self.config_path)
            self.check_mandatory_parameters(mandatory_parameters, temporary_config)
            self.main_config = temporary_config
            self.main_config_md5 = config_md5

            # load modules if configuration file has changed
            self.module_crawler()

    def callback(self, changeType, level, resourceId):
        try:
            self.safe_callback(changeType, level, resourceId)
        except Exception as e:
            orthanc.LogWarning("Error during loading callback : " + str(e))
            print(traceback.format_exc())

    def safe_callback(self, changeType, level, resourceId):
        self.module_crawler()

        if changeType == orthanc.ChangeType.STABLE_SERIES:
            changeType = "Series"
            instances = json.loads(orthanc.RestApiGet("/series/"+resourceId))["Instances"]
        elif changeType == orthanc.ChangeType.STABLE_PATIENT:
            changeType = "Patient"
            studies = json.loads(orthanc.RestApiGet("/patients/"+resourceId))["Studies"]
            series = []
            for s in studies:
                series += json.loads(orthanc.RestApiGet("/studies/"+s))["Series"]
            instances = []
            for s in series:
                instances += json.loads(orthanc.RestApiGet("/series/"+s))["Instances"]
        elif changeType == orthanc.ChangeType.STABLE_STUDY:
            changeType = "Study"
            series = json.loads(orthanc.RestApiGet("/studies/"+resourceId))["Series"]
            instances = []
            for s in series:
                instances += json.loads(orthanc.RestApiGet("/series/"+s))["Instances"]
        else:
            return

        if len(instances) == 0:
            return

        metadata = json.loads(orthanc.RestApiGet("/instances/"+instances[0]+"/metadata?expand"))

        if "CalledAET" not in metadata.keys():
            if metadata["Origin"] == "Plugins":
                if "AutoRemove" in self.main_config.keys():
                    if self.main_config["AutoRemove"]:
                        orthanc.RestApiDelete("/series/"+resourceId) # cleanup already used resources
                return
        else:
            for module in self.modules_list.values():
                if module.config["TriggerLevel"] == changeType:
                    files = []
                    for instanceId in instances:
                        f = orthanc.GetDicomForInstance(instanceId)
                        files += [dcmread(BytesIO(f))]
                    filesfiltered = [i for i in files if module.apply_filters(i)]
                    print(len(filesfiltered),"LEN")

class OrthancAIModule():
    def __init__(self, module_id, module_path):
        self.loaded = False
        self.module_id = module_id
        self.module_path = module_path
        self.module_md5 = None
        self.config_path = module_path.replace(".py",".json")
        self.config_md5 = None
        self.config = {}
        self.module_lib = None
        self.module_class = None
        self.module_instance = None
        self.load_config()

    def check_mandatory_parameters(self, list_parameters):
        for p in list_parameters:
            if p not in self.config.keys():
                raise Exception("Please specify mandatory `" + p + "` parameter for " + self.module_id + " module")

    def load_config(self):
        if not os.path.exists(self.module_path):
            raise Exception("Cannot load find ``" + self.module_path + "``")
        if not os.path.exists(self.config_path):
            raise Exception("Cannot load find ``" + self.config_path + "``")
        self.config = clean_json(self.config_path)
        self.config_md5 = md5_file(self.config_path)
        self.check_mandatory_parameters(mandatory_module_parameters)
        if self.config["TriggerLevel"] not in authorized_triggers:
            raise Exception("Invalid `TriggerLevel` parameter for " + self.module_id + " module")
        self.load_module()

    def load_module(self):
        if self.loaded:
            raise Exception("Please unload module before loading it")
        self.module_md5 = md5_file(self.module_path)
        self.module_lib = imp.load_source(self.module_id, self.module_path)
        self.module_class = getattr(self.module_lib, self.config["ClassName"])
        self.module_instance = self.module_class(self.config)
        orthanc.LogWarning("Loaded module ``" + self.module_id + "``")
        self.loaded = True

    def check_module_update(self):
        if self.config_md5 != md5_file(self.config_path):
            del self.module_lib, self.module_class, self.module_instance, self.config
            self.module_md5 = None
            self.config_md5 = None
            self.loaded = False
            orthanc.LogWarning("Reloading config and module `" + self.module_id + "`...")
            self.load_config()
        elif self.module_md5 != md5_file(self.module_path):
            del self.module_lib, self.module_class, self.module_instance
            self.module_md5 = None
            self.loaded = False
            orthanc.LogWarning("Reloading module `" + self.module_id + "`...")
            self.load_module()


    def apply_filters(self, file):
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
                            return False # the attribute did not match with any positive filter
                    else:
                        return False # The image does not have required attribute
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
    def __bool__(self):
        return self.loaded

oia = OrthancAI(config_path)
orthanc.RegisterOnChangeCallback(oia.callback)