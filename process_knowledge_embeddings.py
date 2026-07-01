#!/usr/bin/env python3
"""
Process knowledge base .txt files and convert them to embeddings.
Saves embeddings to the database for semantic search and AI agent context.

Usage:
    python process_knowledge_embeddings.py          # Process all .txt files
    python process_knowledge_embeddings.py --clear  # Clear existing embeddings first
    python process_knowledge_embeddings.py --file "knowledge/specific.txt"  # Process single file
"""

import os
import sys
import hashlib
import argparse
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.models import KnowledgeEmbedding
from app.extensions import db

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    print("ERROR: sentence-transformers not installed")
    print("Install with: pip install sentence-transformers")
    sys.exit(1)


class KnowledgeEmbeddingProcessor:
    def __init__(self, model_name="sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model_name
        print(f"Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        print(f"Model loaded successfully")
        
    def compute_file_hash(self, content: str) -> str:
        """Generate SHA256 hash of file content for deduplication"""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def process_file(self, file_path: str) -> dict:
        """
        Read a .txt file and generate embeddings
        
        Returns:
            dict with keys: filename, content_preview, embedding_vector, 
                           content_hash, word_count, file_size_bytes
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if not file_path.endswith('.txt'):
            raise ValueError(f"Only .txt files supported. Got: {file_path}")
        
        # Read file
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read().strip()
        
        if not content:
            raise ValueError(f"File is empty: {file_path}")
        
        # Compute metrics
        content_hash = self.compute_file_hash(content)
        word_count = len(content.split())
        file_size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)
        
        # Get preview (first 500 chars)
        content_preview = content[:500] if len(content) > 500 else content
        
        # Generate embedding
        print(f"  Generating embedding for: {filename} ({word_count} words)...")
        embedding = self.model.encode(content)
        
        # Convert embedding to comma-separated string for storage
        embedding_str = ','.join(map(str, embedding))
        
        return {
            'filename': filename,
            'content_preview': content_preview,
            'content_hash': content_hash,
            'embedding_vector': embedding_str,
            'word_count': word_count,
            'file_size_bytes': file_size,
            'embedding_model': self.model_name
        }
    
    def save_embedding(self, embedding_data: dict) -> bool:
        """Save embedding to database, skip if content already exists"""
        try:
            # Check if this content hash already exists
            existing = KnowledgeEmbedding.query.filter_by(
                content_hash=embedding_data['content_hash']
            ).first()
            
            if existing:
                print(f"  ⊘ Skipping (already in DB): {embedding_data['filename']}")
                return False
            
            # Create new embedding record
            embedding_record = KnowledgeEmbedding(
                filename=embedding_data['filename'],
                content_preview=embedding_data['content_preview'],
                content_hash=embedding_data['content_hash'],
                embedding_vector=embedding_data['embedding_vector'],
                embedding_model=embedding_data['embedding_model'],
                file_size_bytes=embedding_data['file_size_bytes'],
                word_count=embedding_data['word_count']
            )
            
            db.session.add(embedding_record)
            db.session.commit()
            print(f"  ✓ Saved to DB: {embedding_data['filename']} (ID: {embedding_record.id})")
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"  ✗ Error saving {embedding_data['filename']}: {str(e)}")
            return False


def process_knowledge_folder(app, processor, knowledge_folder="knowledge", clear=False):
    """Process all .txt files in knowledge folder"""
    
    if not os.path.exists(knowledge_folder):
        print(f"ERROR: Knowledge folder not found: {knowledge_folder}")
        print(f"Create it with: mkdir {knowledge_folder}")
        return
    
    # Clear existing embeddings if requested
    if clear:
        print("Clearing existing embeddings...")
        count = KnowledgeEmbedding.query.delete()
        db.session.commit()
        print(f"  Deleted {count} embeddings")
    
    # Find all .txt files
    txt_files = list(Path(knowledge_folder).glob('*.txt'))
    
    if not txt_files:
        print(f"No .txt files found in {knowledge_folder}/")
        return
    
    print(f"\nProcessing {len(txt_files)} file(s) from {knowledge_folder}/")
    print("-" * 60)
    
    saved_count = 0
    error_count = 0
    
    for txt_file in sorted(txt_files):
        try:
            embedding_data = processor.process_file(str(txt_file))
            if processor.save_embedding(embedding_data):
                saved_count += 1
        except Exception as e:
            print(f"  ✗ Error processing {txt_file.name}: {str(e)}")
            error_count += 1
    
    print("-" * 60)
    print(f"\nSummary:")
    print(f"  ✓ Saved: {saved_count}")
    print(f"  ⊘ Already exist: {len(txt_files) - saved_count - error_count}")
    print(f"  ✗ Errors: {error_count}")
    print(f"  Total files in DB: {KnowledgeEmbedding.query.count()}")


def process_single_file(app, processor, file_path):
    """Process a single file"""
    try:
        embedding_data = processor.process_file(file_path)
        if processor.save_embedding(embedding_data):
            print(f"\n✓ Successfully processed: {file_path}")
        else:
            print(f"\n⊘ File already exists in database")
    except Exception as e:
        print(f"\n✗ Error: {str(e)}")


def main():
    parser = argparse.ArgumentParser(
        description="Process knowledge base .txt files into embeddings"
    )
    parser.add_argument(
        '--clear',
        action='store_true',
        help='Clear all existing embeddings before processing'
    )
    parser.add_argument(
        '--file',
        help='Process a single file (path relative to backend folder)'
    )
    parser.add_argument(
        '--folder',
        default='knowledge',
        help='Knowledge folder path (default: knowledge/)'
    )
    
    args = parser.parse_args()
    
    # Create Flask app context
    app = create_app()
    
    with app.app_context():
        # Initialize processor
        processor = KnowledgeEmbeddingProcessor()
        
        # Process files
        if args.file:
            process_single_file(app, processor, args.file)
        else:
            process_knowledge_folder(app, processor, args.folder, args.clear)


if __name__ == '__main__':
    main()
