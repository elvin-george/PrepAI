import os
import sys
import shutil
from PyPDF2 import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter 
from langchain_huggingface import HuggingFaceEmbeddings # <--- Switch to Local
from langchain_community.vectorstores import FAISS
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
load_dotenv()

def build_index(pdf_folder="knowledge_base"):
    print("🚀 Starting Local RAG Builder...")
    
    # 1. Clean up old index (Crucial: Google embeddings are incompatible with Local ones)
    if os.path.exists("faiss_index"):
        shutil.rmtree("faiss_index")
        print("🗑️  Deleted old Google-based index.")

    # 2. Read PDFs
    print("📂 Scanning 'knowledge_base'...")
    text = ""
    if not os.path.exists(pdf_folder):
        os.makedirs(pdf_folder)
        print(f"❌ Folder '{pdf_folder}' missing. Created it.")
        return

    pdf_count = 0
    for filename in os.listdir(pdf_folder):
        if filename.endswith('.pdf'):
            try:
                pdf_path = os.path.join(pdf_folder, filename)
                pdf_reader = PdfReader(pdf_path)
                for page in pdf_reader.pages:
                    text += page.extract_text() or ""
                pdf_count += 1
                print(f"   - Loaded: {filename}")
            except Exception as e:
                print(f"   - ⚠️ Skipped {filename}: {e}")

    if not text:
        print("❌ No readable text found in PDFs.")
        return

    # 3. Chunk Text
    print("✂️  Splitting text...")
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = text_splitter.split_text(text)
    print(f"   - Created {len(chunks)} chunks.")

    # 4. Generate Embeddings (Locally)
    print("🧠 Generating Embeddings (using local 'all-MiniLM-L6-v2')...")
    # This runs on your CPU - no API key needed, no rate limits!
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    
    vector_store = FAISS.from_texts(chunks, embedding=embeddings)
    
    # 5. Save
    vector_store.save_local("faiss_index")
    print("🎉 SUCCESS: Local Index created! No API quota used.")

if __name__ == "__main__":
    build_index()