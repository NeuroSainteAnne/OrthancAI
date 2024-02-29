import sys
import os
import pydicom
import glob
import numpy as np
from skimage import filters, morphology
from scipy.ndimage.morphology import binary_fill_holes
from dipy.segment.mask import median_otsu
from nipy.labs.mask import largest_cc
import tensorflow as tf
import traceback
import copy
from PIL import Image
from PIL import ImageDraw
import orthanc
import io
import json
from tools import add_text_to_dicom, rename_series

class SynthFlair():
    def __init__(self, config):
        self.config = config
        if self.config["synthflair_generator_path"]:
            self.synthflair_generator = tf.keras.models.load_model(self.config["synthflair_generator_path"])
        else:
            self.synthflair_generator = None
        if self.config["syntht2eg_generator_path"]:
            self.syntht2eg_generator = tf.keras.models.load_model(self.config["syntht2eg_generator_path"])
        else:
            self.syntht2eg_generator = None

    def process(self, files, source_aet):
        origFiles, b0, b1000, adc, mask, minb1000, maxb1000 = self.processDWI(files)
        returnFiles = []
        if self.synthflair_generator is not None:
            returnFiles += self.createSynthFlairFiles(origFiles, b0, b1000, adc, mask, minb1000, maxb1000)
        if self.syntht2eg_generator is not None:
            returnFiles += self.createSynthT2egFiles(origFiles, b0, b1000, mask, minb1000, maxb1000)
        return returnFiles

    def padvol(self, volumes, x, y):
        padvol = []
        orig_shape = np.array(list(volumes[0].shape)).astype(float)
        for vol in volumes:
            padvol.append(vol.copy())
        padx1 = padx2 = pady1 = pady2 = 0
        if orig_shape[0] < x or orig_shape[1] < y:
            if orig_shape[0] < x:
                padx1 = int((float(x) - orig_shape[0])/2)
                padx2 = float(x) - orig_shape[0] - padx1
            if orig_shape[1] < y:
                pady1 = int((float(y) - orig_shape[1])/2)
                pady2 = float(y) - orig_shape[1] - pady1
            for ivol in range(len(padvol)):
                padvol[ivol] = np.pad(padvol[ivol], ((padx1, padx2),(pady1,pady2),(0,0)), mode="edge")
        cutx1 = cutx2 = cuty1 = cuty2 = 0
        if orig_shape[0] > x or orig_shape[1] > y:
            if orig_shape[0] > x:
                cutx1 = int((orig_shape[0]-float(x))/2)
                cutx2 = orig_shape[0] - x - cutx1
                for ivol in range(len(padvol)):
                    orthanc.LogWarning(str(i)+":"+str(ivol.shape)+" "+str(cutx1)+str(cutx2))
                    padvol[ivol] = padvol[ivol][cutx1:-cutx2,:,:]
            if orig_shape[1] > y:
                cuty1 = int((orig_shape[1]-float(y))/2)
                cuty2 = orig_shape[1] - y - cuty1
                for ivol in range(len(padvol)):
                    padvol[ivol] = padvol[ivol][:,cuty1:-cuty2,:]
        return tuple(padvol)

    def processDWI(self, files):
        slices = []
        for f in files:
            if hasattr(f, 'SliceLocation'):
                slices.append(f)
        b0 = []
        b1000 = []
        for s in slices:
            if str(s[0x0043, 0x1039][0]) == "0":
                b0.append(s)
            if "1000" in str(s[0x0043, 0x1039][0]):
                b1000.append(s)
        b0 = sorted(b0, key=lambda s: s.SliceLocation)
        b1000 = sorted(b1000, key=lambda s: s.SliceLocation)

        orig_shape = (b0[0].pixel_array.shape) + (len(b0),)
        b0_src = np.zeros(orig_shape)
        for i, s in enumerate(b0):
            orig_dtype = s.pixel_array.dtype
            b0_src[:,:,i] = s.pixel_array
        b1000_src = np.zeros(orig_shape)
        for i, s in enumerate(b1000):
            b1000_src[:,:,i] = s.pixel_array

        b0_padded, b1000_padded = self.padvol([b0_src, b1000_src], 256, 256)

        maskdata = (b0_padded >= 1) & (b1000_padded >= 1) # exclude zeros for ADC calculation
        adc_padded = np.zeros(b0_padded.shape, b0_padded.dtype)
        adc_padded[maskdata] = -1. * float(1000) * np.log(b1000_padded[maskdata] / b0_padded[maskdata])
        adc_padded[adc_padded < 0] = 0

        b0_mask, mask = median_otsu(b0_padded, 1, 1)
        b1000_mask, mask1000 = median_otsu(b1000_padded, 1, 1)
        mask_padded = binary_fill_holes(morphology.binary_dilation(largest_cc(mask & mask1000)))
        mask_padded = mask_padded & (b0_padded >= 1) & (b1000_padded >= 1)

        masked_b0 = b0_padded[mask_padded]
        mean_b0, sd_b0 = np.mean(masked_b0), np.std(masked_b0)
        masked_b1000 = b1000_padded[mask_padded]
        mean_b1000, sd_b1000 = np.mean(masked_b1000), np.std(masked_b1000)

        minb1000 = np.min(masked_b1000)
        maxb1000 = np.max(masked_b1000)

        b0_padded = (b0_padded - mean_b0) / sd_b0
        b1000_padded = (b1000_padded - mean_b1000) / sd_b1000

        b0_padded = ((b0_padded + 5) / (12 + 5))*2-1
        b1000_padded = ((b1000_padded + 5) / (12 + 5))*2-1
        adc_padded = ((adc_padded) / (7500))*2-1
        b0_padded[b0_padded > 1] = 1
        b0_padded[b0_padded < -1] = -1
        b1000_padded[b1000_padded > 1] = 1
        b1000_padded[b1000_padded < -1] = -1

        return b1000, b0_padded, b1000_padded, adc_padded, mask_padded, minb1000, maxb1000

    def createSynthFlairFiles(self, b1000, b0_padded, b1000_padded, adc_padded, mask_padded, minb1000, maxb1000):
        stacked = np.stack([b0_padded,b1000_padded,adc_padded]).transpose([3,2,1,0])[:,:,::-1,np.newaxis,:]
        qualarr = np.tile(2, (stacked.shape[0],1))
        fsarr = np.tile(0, (stacked.shape[0],1))

        synthflair = self.synthflair_generator.predict([stacked, qualarr])[:,:,::-1,0].transpose(2,1,0)

        synthflairmasked = synthflair[mask_padded]
        minsynthflair = np.min(synthflairmasked)
        maxsynthflair = np.max(synthflairmasked)

        synthflair = ((synthflair - minsynthflair)/(maxsynthflair-minsynthflair))*(maxb1000-minb1000)+minb1000

        flairfiles = copy.deepcopy(b1000)
        for i in range(len(flairfiles)):
            flairfiles[i].PixelData = synthflair[:,:,i].astype(flairfiles[i].pixel_array.dtype).tobytes()
        flairfiles = add_text_to_dicom(flairfiles, "SynthFLAIR - not for diagnostic use", 10)
        flairfiles = rename_series(flairfiles, "SynthFLAIR")
        return flairfiles

    def createSynthT2egFiles(self, b1000, b0_padded, b1000_padded, mask_padded, minb1000, maxb1000):
        stacked = np.stack([b0_padded,b1000_padded]).transpose([3,2,1,0])[:,:,::-1,np.newaxis,:]
        qualarr = np.tile(3, (stacked.shape[0],1))
        fsarr = np.tile(0, (stacked.shape[0],1))

        t2eg = self.syntht2eg_generator.predict([stacked, qualarr,fsarr])[0][:,:,::-1,0].transpose(2,1,0)

        t2egmasked = t2eg[mask_padded]
        mint2eg = np.min(t2egmasked)
        maxt2eg = np.max(t2egmasked)

        t2eg = ((t2eg - mint2eg)/(maxt2eg-mint2eg))*(maxb1000-minb1000)+minb1000

        t2egfiles = copy.deepcopy(b1000)
        for i in range(len(t2egfiles)):
            t2egfiles[i].PixelData = t2eg[:,:,i].astype(t2egfiles[i].pixel_array.dtype).tobytes()
        t2egfiles = add_text_to_dicom(t2egfiles, "SynthT2eg - not for diagnostic use", 10)
        t2egfiles = rename_series(t2egfiles, "SynthT2eg")
        return t2egfiles
