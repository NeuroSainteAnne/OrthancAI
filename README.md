# OrthancAI

## Summary
- [What is OrthancAI](#what-is-orthancai)
- [Installation](#how-to-install-orthancai)
- [Configuration](#configure-orthanc-python-plugin-and-orthanc-ai)
- [OrthancAI modules](#structure-of-orthancai-modules)
- [Tools](#tools-module)

## What is OrthancAI?

**OrthancAI** is a wraparound around [Orthanc](https://orthanc.uclouvain.be/) and [Orthanc Python plugin](https://orthanc.uclouvain.be/book/plugins/python.html), tailored for the deployment of fast and custom AI inference modules in a clinical workflow.

The advantages of **OrthancAI** are:

- Modular conception, allowing the deployment of multiple image post-treatment modules
- Possibility of preloading models in RAM (*Module Caching*), allowing fast GPU or CPU inference as soon as DICOM files are received
- Cleanup system, allowing a temporary storage of processed files, thus increasing safety and reducing storage requirements
- Dynamic module loading, allowing to hot-change modules parameters and code source without restarting Orthanc

<img src="doc/OrthancAIWorkflow.jpg" width="800" alt="OrthancAI global architecture design">


## How to install OrthancAI

1. Install on your own server
   - Install a standard Linux distribution (such as Ubuntu)
   - Install Python (>=3.9)
     ```
     sudo apt update
     sudo apt install python3 python3-pip
     ```
   - Install Orthanc and its Python plugin
     ```
     sudo apt install orthanc orthanc-dev orthanc-python
     ```
   - Download OrthancAI latest release and copy files in **/etc/orthanc**
   - Install python requirements
     ```
     pip install -r requirements.txt
     ```
   - Start Orthanc
     ```
     sudo /usr/sbin/Orthanc /etc/orthanc
     ```
     
2. Install Docker image : *TODO*

## Configure Orthanc, Python plugin and Orthanc AI

### Configure Orthanc

To configure your Orthanc server, you can check official [Orthanc documentation](https://orthanc.uclouvain.be/book/users/configuration.html#configuration). We recommend you set the following parameters in the **/etc/orthanc/orthanc.json** file for optimal compatibility:

- Enable REST API which is necessary for OrthancAI to work :
`"HttpServerEnabled" : true`

- Activate DICOM server for obvious reasons
`"DicomServerEnabled" : true`

- Enable multiple AET for multiple modules :
`"DicomCheckCalledAet" : false`

- Set the port of your choice for DICOM incoming requests:
`"DicomPort" : 8042`

- Under the **"DicomModality"** header, add your PACS destination.
`"DestinationName" : {"AET" : "AET", "Host" : "123.456.789.0", "Port" : 1234 }`

- Number of seconds for the series/study/patient considered as stable (I usually set it to 5 seconds)
`"StableAge" : 5`

### Configure Python plugin

In the **/etc/orthanc/python.json** file, set the following parameter:
`"PythonScript" : "/etc/orthanc/orthanc_ai.py"`

### Configure OrthancAI

Configuration of **/etc/orthanc/orthanc_ai.json** is pretty straightforward.

- *ModuleLoadingHeuristic* defines how the modules are named and searched (using wildcards)
- *AutoRemove* defines if files are deleted after being processed
- *AutoReloadEach* defines the frequency (in seconds) at which all the modules will be checked for update. Note that this check will also be performed at each image reception, but autoreload could improve performance if module loading is slow. Set to 0 to deactivate

### Configure OrthancAI modules

Each OrthancAI module, located in the *oai_modules* directory, has its own  mandatory parameters. Other parameters, optional to each module, can also be proposed. These are mandatory parameters: 

- *ClassName* : must correspond to the main class name defined in the module (oai_xxx.py)
- *TriggerLevel* : defines with what data the module is called. Can be **Patient**, **Study** or **Series**
- *CallingAET* : the AET name that will trigger module processing. A same AET can be used on several modules
- *DestinationName* : the destination name where the returned files will be pushed. Note that it is an *orthanc destination name* as defined in the orthanc configuration file, not the AET
- *Filters* and *NegativeFilters* are array containing filters allowing to decide if the processing will be performed or not. The filters are applied to each individual files. It should be given as a dictionary of lists. Dictionary keys can be any of the following DICOM tags :
```
AccessionNumber, PatientName, PatientID, StudyDescription, SeriesDescription, ImageType, InstitutionName, InstitutionalDepartmentName, Manufacturer, ManufacturerModelName, Modality, OperatorsName, PerformingPhysicianName, ProtocolName, StudyID
```
Each filter shall have the following structure:
```
  "Filters": {
    "PatientName": ["Test","Or*Another"],
    "SeriesDescription": ["Test"]
  },
```
For **Positive** filters, each key should match with one of the strings given (regular expressions allowed). For **Negative** filters, each key should NOT match with any of the strings given.

## Structure of OrthancAI modules

You're now ready to deploy your first module! You can have a look at *oia_test.py* module which is a simple example that capture a series and adds a simple white text in its top-left corner.

Each module contains at least two files :

- a configuration file, **oai_xxx.json** that contains the mandatory parameters (cf supra) and optional parameters that can be used in the module
- a python module file, which should be structured around a class (whose name is defined in the configuration file). The structure of the class should be the following :

```
class ModuleName():
    def __init__(self, config):
        self.config = config

    def process(self, files, source_aet):
        # do something with files
        return files
```

The **\_\_init\_\_** subroutine will be called at each module reload (in case of modification of the config files or the module python script). All variables relative to the module should be defined here, in particular if you have to load a machine learning model, you should do it here (so that it will be ready to use during processing time). This subroutine is called wirth a *config* variable which is simply the pythonized content of the json configuration file.

The **process** subroutine will be called each time a matching exam is send to the orthanc server. The *files* variable contains the dicom files (in [pydicom](https://pydicom.github.io/) format) as an array, whose structure is dependent on the *TriggerLevel* parameter in configuration file:

- if the *TriggerLevel* is "Series", it will be simply a flat array of dicom files of the sent series `[file1, file2]`
- if the *TriggerLevel* is "Study", it will be simply a collection of arrays, each array containing a whole series `[[series1_file1, series1_file2],[series2_file1,series2_file2]]`
- if the *TriggerLevel* is "Patient", it will be simply a collection of arrays, each array containing a whole study, with nested series `[[[study1_series1_file1, study1_series1_file2],[study1_series2_file1,study1_series2_file2]],[[study2_series_1_file_1],[study2_series2_file_1]]]`

The **process** subroutine is also called with a *source_aet* parameter that is the origin from the files.

At last, the **process** subroutine should return a list of pydicom files that will be sent to the DICOM destination defined in the configuration file (or *None* if it is not necessary). Be aware that if you send back some series, you should modify series so that there will be no conflict with original series... but for that, the **tools** can help you !

## Tools module

**OrthancIA** comes with a number of tools that you can call with the `import tools` command. These include :

- `push_files_to(files, destination)`, will send the *files* dicom files to the *destination* orthance destination
- `push_PILImage_in_DICOM(dcmfile, PILImage)` that will allow to convert a [PILImage](https://pillow.readthedocs.io/) into JPEG and encapsulate it in a *dcmfile* dicom file
- `add_text_to_dicom(dcmfiles, textvalue, [fontsize=24])` that will add white text to a dicom or several dicom files
- `rename_series(dcmfiles, textvalue)` : allows not only to prepend a *textvalue* text to the name of a series but also change its UID so that it can be pushed back onto your PACS without confict
