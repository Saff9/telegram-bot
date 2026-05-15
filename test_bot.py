import os
import sys

def test_structure():
    print("Testing file structure...")
    files = ["bot.py", "requirements.txt", "Dockerfile", "docker-compose.yml", ".env.example"]
    all_found = True
    for f in files:
        if os.path.exists(f):
            print(f"[OK] {f} found.")
        else:
            print(f"[ERROR] {f} missing.")
            all_found = False
    return all_found

if __name__ == "__main__":
    if test_structure():
        print("\nSUCCESS: All core files are present.")
    else:
        print("\nFAILURE: Some core files are missing.")
        sys.exit(1)
