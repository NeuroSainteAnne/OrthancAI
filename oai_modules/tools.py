import numpy as np
from pydicom.uid import RLELossless

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