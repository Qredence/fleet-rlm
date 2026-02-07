#!/usr/bin/env python3
"""Generate test data for RLM long-context skill evaluation."""

import random
from datetime import datetime, timedelta


def generate_log_file(filepath, lines=50000):
    """Generate test log with known error distribution."""
    # Ground truth: 50 ERROR, 200 WARN, ~49750 INFO
    levels = ['INFO'] * 995 + ['WARN'] * 4 + ['ERROR'] * 1
    error_types = ['timeout', 'connection', 'auth', 'disk_full', 'memory']
    
    error_count = 0
    warn_count = 0
    first_timeout = None
    
    with open(filepath, 'w') as f:
        timestamp = datetime(2024, 1, 1, 10, 0, 0)
        
        for i in range(lines):
            level = random.choice(levels)
            if level == 'ERROR':
                error_type = random.choice(error_types)
                msg = f"ERROR: {error_type} failed"
                error_count += 1
                if error_type == 'timeout' and first_timeout is None:
                    first_timeout = timestamp.strftime('%Y-%m-%d %H:%M:%S')
            elif level == 'WARN':
                msg = "WARN: Retrying operation"
                warn_count += 1
            else:
                msg = "INFO: Operation successful"
            
            f.write(f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            timestamp += timedelta(seconds=random.randint(1, 10))
    
    # Save ground truth
    import json
    ground_truth = {
        "error_count": error_count,
        "warn_count": warn_count,
        "first_timeout": first_timeout,
        "total_lines": lines,
        "error_types": error_types
    }
    with open(filepath + '.ground_truth.json', 'w') as gt:
        json.dump(ground_truth, gt, indent=2)
    
    print(f"Generated {filepath}:")
    print(f"  Total lines: {lines}")
    print(f"  ERROR: {error_count}")
    print(f"  WARN: {warn_count}")
    print(f"  First timeout: {first_timeout}")
    
    return ground_truth


def generate_corpus(filepath, lines=200000):
    """Generate text corpus with 5% keyword density."""
    keywords = ['target_keyword', 'important', 'critical', 'error']
    filler = ['the', 'quick', 'brown', 'fox', 'jumps', 'over', 'lazy', 'dog']
    
    keyword_count = 0
    
    with open(filepath, 'w') as f:
        for i in range(lines):
            if random.random() < 0.05:  # 5% density
                word = random.choice(keywords)
                keyword_count += 1
            else:
                word = random.choice(filler)
            f.write(f"{word} " * 20 + "\n")
    
    print(f"Generated {filepath}:")
    print(f"  Total lines: {lines}")
    print(f"  Keyword occurrences: {keyword_count} (~5%)")
    
    return keyword_count


if __name__ == "__main__":
    import sys
    import os
    
    # Create test_data directory
    os.makedirs("test_data", exist_ok=True)
    
    print("Generating test data...\n")
    
    # Generate test files
    gt = generate_log_file("test_data/test_data.log", 50000)
    print()
    kw_count = generate_corpus("test_data/large_corpus.txt", 200000)
    
    print("\nTest data generation complete!")
    print("Ground truth saved to: test_data/test_data.log.ground_truth.json")
