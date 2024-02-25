# OrthancAI

## What is OrthancAI ?

**OrthancAI** is a wraparound around [Orthanc](https://orthanc.uclouvain.be/) and [Orthanc Python plugin](https://orthanc.uclouvain.be/book/plugins/python.html), tailored for the deployment of fast and custom AI inference modules in a clinical workflow.

The advantages of **OrthancAI** are:

- Modular conception, allowing the deployment of multiple image post-treatment modules
- Possibility of preloading models in RAM (*Module Caching*), allowing fast GPU or CPU inference as soon as DICOM files are received
- Cleanup system, allowing a temporary storage of post-treated files, thus increasing safety and reducing storage requirements
- Dynamic module loading, allowing to hot-change modules parameters and code source without restarting Orthanc

![OrthancAI global architecture design](doc/OrthancAIWorkflow.jpg)
