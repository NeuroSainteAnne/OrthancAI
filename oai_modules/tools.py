import numpy as np
from pydicom.uid import RLELossless
import hashlib
import re
import json
from io import BytesIO
import orthanc
from PIL import Image, ImageDraw, ImageFont
import pydicom

def push_files_to(files, destination):
    if type(files) is not list:
        files = [files]
    files = flatten(files)
    instances = []
    for f in files:
        bytesfile = BytesIO()
        f.save_as(bytesfile)
        instanceinfo = json.loads(orthanc.RestApiPost("/instances", bytesfile.getvalue()))
        del bytesfile
        instances += [instanceinfo["ID"]]
    push_instances_to(instances, destination)

def push_instances_to(instances, destination):
    instances = flatten(instances)
    postString = json.dumps({"Resources":instances})
    orthanc.RestApiPost("/modalities/" + destination + "/store", postString)

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

def flatten_gen(mylist):
    for i in mylist:
        if isinstance(i, (list,tuple)):
            for j in flatten(i): yield j
        else:
            yield i

def flatten(mylist):
    return list(flatten_gen(mylist))

def push_PILImage_in_DICOM(dcmfile, PILImage):
    pixArr = np.array(PILImage).astype(np.uint8)
    dcmfile.SamplesPerPixel = 3
    dcmfile.SamplesPerPixel = 3
    dcmfile.PhotometricInterpretation = 'RGB'
    dcmfile.BitsAllocated = 8
    dcmfile.BitsStored = 8
    dcmfile.PixelRepresentation = 0
    dcmfile.Rows = pixArr.shape[0]
    dcmfile.Columns = pixArr.shape[1]
    dcmfile.RescaleIntercept = 0
    dcmfile.RescaleSlope = 1
    dcmfile.PixelData = pixArr.tobytes()
    dcmfile.compress(RLELossless)
    return dcmfile

def add_text_to_dicom(dcmfiles, textvalue, fontsize=24):
    singleFile = False
    if type(dcmfiles) != list:
        dcmfiles = list(dcmfiles)
        singleFile = True

    img = Image.new("L",(dcmfiles[0].Rows,dcmfiles[0].Columns))
    font = ImageFont.truetype("FreeMono.ttf", fontsize)
    d = ImageDraw.Draw(img)
    d.text((5, 5), textvalue, fill=255, font=font)
    textarray = 1-np.array(img).astype(float)/255.0

    dcmconv = []
    dcm_max = -1000
    for dcmfile in dcmfiles:
        dcm_max = max(dcm_max, np.max(dcmfile.pixel_array))
    for dcmfile in dcmfiles:
        dcm_data = dcmfile.pixel_array
        dcm_newdata = ((dcm_data-dcm_max)*textarray)+dcm_max
        dcmfile.PixelData = dcm_newdata.astype(dcm_data.dtype).tobytes()
        dcmconv += [dcmfile]
    if singleFile: return dcmconv[0]
    return dcmconv

def rename_series(dcmfiles, textvalue):
    singleFile = False
    if type(dcmfiles) != list:
        dcmfiles = list(dcmfiles)
        singleFile = True
    dcmconv = []
    uidStudy = pydicom.uid.generate_uid()
    uidPrefix = pydicom.uid.generate_uid()[:-3]
    for i, s in enumerate(dcmfiles):
        s.SeriesDescription = textvalue + " - " + s.SeriesDescription
        s.SOPInstanceUID = uidPrefix + str(i).zfill(3)
        s.file_meta.MediaStorageSOPInstanceUID = uidPrefix + str(i).zfill(3)
        s.SeriesInstanceUID = uidStudy
        s.SeriesNumber = 1000 + s.SeriesNumber
        s.InstanceNumber = i
        dcmconv += [s]
    if singleFile: return dcmconv[0]
    return dcmconv
