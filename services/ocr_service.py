import os
import pytesseract
from PIL import Image

def perform_ocr(file_path: str) -> str:
    """
    Extracts text from an image file using Tesseract OCR.
    """
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        image = Image.open(file_path)
        image = image.convert('RGB')
        
        text = pytesseract.image_to_string(image)
        return text.strip()
    except Exception as e:
        print(f"OCR Error: {e}")
        raise e
