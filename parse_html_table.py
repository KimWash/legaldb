#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
HTML table parser script to extract original and changed filenames.
Reads an HTML table (from a file or stdin), extracts 'cell-name' columns,
and outputs a CSV mapping original filenames to changed filenames.
"""

import os
import sys
import csv
from html.parser import HTMLParser

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current_row = []
        self.in_row = False
        
        self.in_cell = False
        self.cell_title = ""
        self.cell_data = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        tag = tag.lower()
        
        if tag == 'tr':
            self.in_row = True
            self.current_row = []
        elif tag == 'td' and self.in_row:
            classes = attrs_dict.get('class', '').split()
            if 'cell-name' in classes:
                self.in_cell = True
                self.cell_title = attrs_dict.get('title', '')
                self.cell_data = []

    def handle_data(self, data):
        if self.in_cell:
            self.cell_data.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'tr' and self.in_row:
            self.in_row = False
            if self.current_row:
                self.rows.append(self.current_row)
        elif tag == 'td' and self.in_cell:
            self.in_cell = False
            text_content = "".join(self.cell_data).strip()
            self.current_row.append({
                'title': self.cell_title,
                'text': text_content
            })

def get_filename_from_path(path):
    if not path:
        return ""
    # Normalize path separators to forward slash
    normalized = path.replace('\\', '/')
    return normalized.split('/')[-1]

def parse_html_to_csv(html_content, csv_output_path):
    parser = TableParser()
    parser.feed(html_content)
    
    records = []
    
    for row in parser.rows:
        # We need at least two columns with class 'cell-name'
        # The first is original, the second is changed
        if len(row) >= 2:
            orig_cell = row[0]
            changed_cell = row[1]
            
            orig_path = orig_cell['title']
            orig_filename = orig_cell['text'] or get_filename_from_path(orig_path)
            
            changed_path = changed_cell['title']
            changed_filename = changed_cell['text'] or get_filename_from_path(changed_path)
            
            records.append({
                'Original Path': orig_path,
                'Original Filename': orig_filename,
                'Changed Path': changed_path,
                'Changed Filename': changed_filename
            })
    
    # Write to CSV
    headers = ['Original Path', 'Original Filename', 'Changed Path', 'Changed Filename']
    try:
        with open(csv_output_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(records)
        return len(records)
    except Exception as e:
        print(f"Error writing CSV file: {e}", file=sys.stderr)
        return -1

def main():
    print("=" * 60)
    print(" HTML Table to CSV Parser (Filename Mapping Extractor) ")
    print("=" * 60)
    
    # Default paths
    default_input_file = "table.html"
    default_output_file = "filename_mapping.csv"
    
    input_content = ""
    output_path = default_output_file
    
    # Check arguments
    if len(sys.argv) > 1:
        arg1 = sys.argv[1]
        if arg1 in ('-h', '--help'):
            print("Usage:")
            print("  1. Read from a file:")
            print("     python parse_html_table.py <input_html_file> [output_csv_file]")
            print("  2. Read from standard input (stdin):")
            print("     python parse_html_table.py - [output_csv_file]")
            print("  3. Run interactively (paste HTML):")
            print("     python parse_html_table.py")
            return
            
        if arg1 == '-':
            # Read from stdin
            print("Reading HTML content from standard input (Ctrl+Z and Enter on Windows to finish)...")
            input_content = sys.stdin.read()
        else:
            # Read from file
            if os.path.exists(arg1):
                print(f"Reading from file: {arg1}")
                with open(arg1, 'r', encoding='utf-8') as f:
                    input_content = f.read()
            else:
                print(f"Error: Input file '{arg1}' not found.", file=sys.stderr)
                sys.exit(1)
                
        if len(sys.argv) > 2:
            output_path = sys.argv[2]
    else:
        # Check if table.html exists in current directory
        if os.path.exists(default_input_file):
            print(f"Found default input file: {default_input_file}")
            with open(default_input_file, 'r', encoding='utf-8') as f:
                input_content = f.read()
        else:
            # Prompt or read from stdin
            print(f"Default input file '{default_input_file}' not found.")
            print("Please paste the HTML table content below. When finished:")
            print(" - On Windows: Press Ctrl+Z, then Enter.")
            print(" - On Linux/macOS: Press Ctrl+D.")
            print("-" * 60)
            input_content = sys.stdin.read()
            print("-" * 60)

    if not input_content.strip():
        print("Error: No HTML content provided.", file=sys.stderr)
        sys.exit(1)

    print("Parsing HTML and extracting file mapping...")
    count = parse_html_to_csv(input_content, output_path)
    
    if count >= 0:
        print(f"Success! Extracted {count} rows and saved to '{output_path}'.")
        print(f"Absolute output path: {os.path.abspath(output_path)}")
    else:
        print("Failed to generate CSV file.", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
