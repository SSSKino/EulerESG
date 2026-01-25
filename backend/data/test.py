import camelot

try:
    print(f"Camelot version: {camelot.__version__}")
    if hasattr(camelot, 'read_pdf'):
        print("✅ Successfully imported camelot and found the 'read_pdf' function!")
    else:
        print("❌ ERROR: Could not find 'read_pdf' function.")
except Exception as e:
    print(f"An error occurred: {e}")
