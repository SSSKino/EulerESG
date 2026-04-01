import pytesseract
import pandas as pd
from PIL import Image
import re
import io

def extract_table_from_image(image_path: str) -> pd.DataFrame:
    """
    Extracts a table from an image using OCR and structures it into a pandas DataFrame.
    """
    try:
        # Open the image file
        image = Image.open(image_path)
        
        # --- OCR Configuration ---
        # --psm 6 (Page Segmentation Mode 6) is crucial here.
        # It assumes the image is a single uniform block of text, which is perfect for a table.
        config = r'--psm 6'

        # Use pytesseract to extract the raw text
        raw_text = pytesseract.image_to_string(image, config=config)
        
        print("--- Raw OCR Output ---")
        print(raw_text)
        print("----------------------")

        # --- Text Processing and Structuring ---
        
        # Use StringIO to treat the raw text block as a file, which pandas can read
        data = io.StringIO(raw_text)
        
        # Read the text into a DataFrame. We use a regex separator `\s{2,}`
        # which means "split by two or more whitespace characters".
        # This is robust for handling table columns separated by spaces.
        df = pd.read_csv(data, sep=r'\s{2,}', engine='python', header=0)
        
        # A common OCR issue is misinterpreting the header. Let's clean it up.
        # This example assumes the first line is the header, which might need adjustment.
        
        return df

    except FileNotFoundError:
        print(f"Error: The file at {image_path} was not found.")
        return pd.DataFrame()
    except Exception as e:
        print(f"An error occurred: {e}")
        return pd.DataFrame()

# --- Example Usage ---
if __name__ == "__main__":
    # Replace with the actual path to your PNG image
    image_file_path = "/home/nana/Downloads/summary-table.png" 
    
    table_df = extract_table_from_image(image_file_path)
    
    if not table_df.empty:
        print("\n--- Extracted and Structured DataFrame ---")
        print(table_df)
        
        # You can now easily process this DataFrame further or convert to JSON
        # json_output = table_df.to_json(orient='records', indent=2)
        # print("\n--- JSON Output ---")
        # print(json_output)

