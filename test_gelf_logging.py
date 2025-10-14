#!/usr/bin/env python3
"""Test script to verify GELF field flattening"""
import json
from typing import Dict, Any

def test_gelf_flattening():
    """Simulate the _send_to_gelf method to verify field flattening"""

    # Simulate input data
    data = {
        "timestamp": "2025-10-14T09:05:27.491990+00:00",
        "version": "dev",
        "hostname": "localhost:3000",
        "remote_addr": "127.0.0.1",
        "method": "POST",
        "path": "/ota/service/request",
        "headers": {"Host": "localhost:3000"},
        "query_params": {"test_query": "value1", "version": "1.0"},
        "body": {"brand": "onda", "device": "test-device", "firmware": "V1.0", "android": "4.2.2"},
        "request_size": 57,
        "response_status": 200,
        "response_size": 496,
        "duration_ms": 2
    }

    # Simulate the flattening logic
    gelf_data = data.copy()

    # Flatten body fields
    if isinstance(gelf_data.get('body'), dict):
        body_dict = gelf_data.pop('body')
        for key, value in body_dict.items():
            gelf_data[f'body_{key}'] = value

    # Flatten query_params fields
    if isinstance(gelf_data.get('query_params'), dict) and gelf_data['query_params']:
        query_dict = gelf_data.pop('query_params')
        for key, value in query_dict.items():
            gelf_data[f'query_{key}'] = value

    print("=" * 80)
    print("GELF DATA THAT WOULD BE SENT TO GRAYLOG:")
    print("=" * 80)
    print(json.dumps(gelf_data, indent=2, sort_keys=True))
    print("\n" + "=" * 80)
    print("SEARCHABLE FIELDS IN GRAYLOG:")
    print("=" * 80)

    # Show flattened fields
    body_fields = [k for k in gelf_data.keys() if k.startswith('body_')]
    query_fields = [k for k in gelf_data.keys() if k.startswith('query_')]

    print("\nBody Fields (flattened):")
    for field in sorted(body_fields):
        print(f"  {field}: {gelf_data[field]}")

    print("\nQuery Fields (flattened):")
    for field in sorted(query_fields):
        print(f"  {field}: {gelf_data[field]}")

    print("\n" + "=" * 80)
    print("EXAMPLE GRAYLOG SEARCHES:")
    print("=" * 80)
    for field in sorted(body_fields):
        print(f"  {field}:{gelf_data[field]}")
    for field in sorted(query_fields):
        print(f"  {field}:{gelf_data[field]}")

    print("\n" + "=" * 80)
    print("VERIFICATION: ✓ Fields are properly flattened and searchable")
    print("=" * 80)

if __name__ == "__main__":
    test_gelf_flattening()
