from tools import add_text_to_dicom, rename_series

class TestOAI():
    def __init__(self, config):
        self.config = config

    def process(self, files, source_aet):
        annotated_dicom = add_text_to_dicom(files, self.config["text"])
        renamed_dicom = rename_series(files, self.config["label"])
        return renamed_dicom