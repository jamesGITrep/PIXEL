import subprocess
import re

CHROME_BIN = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

def get_dom(url):
    cmd = [CHROME_BIN, "--headless=new", "--virtual-time-budget=3000", "--dump-dom", url]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return res.stdout

def main():
    print("Verifying route isolation across public views...")
    public_urls = [
        ("Landing Page", "http://localhost:3000/"),
        ("Live Event Arena", "http://localhost:3000/live"),
        ("Studio Workshop", "http://localhost:3000/studio"),
    ]

    forbidden_patterns = [
        r"ROUTE ISOLATION ACTIVE",
        r"Simulated MIG Node Scaling",
        r"Worker CPU Core Saturation",
        r"GCS Lock Precondition Latency",
        r"Spot VM Preemption & Task Recovery Benchmark",
        r'href=["\']?/admin["\']?',
        r'navigateTo\([\'"]admin[\'"]\)',
    ]

    for label, url in public_urls:
        dom = get_dom(url)
        for pattern in forbidden_patterns:
            matches = re.findall(pattern, dom, re.IGNORECASE)
            assert len(matches) == 0, f"LEAK DETECTED in {label} ({url}): matched '{pattern}'"
        print(f"  [PASS] {label} ({url}): Zero admin telemetry or navigation links detected.")

    print("\nVerifying /admin route rendering...")
    admin_dom = get_dom("http://localhost:3000/admin")
    required_admin_patterns = [
        "ROUTE ISOLATION ACTIVE",
        "1. Simulated MIG Node Scaling Timeline",
        "2. Worker CPU Core Saturation",
        "3. GCS Lock Precondition Latency",
        "Spot VM Preemption"
    ]
    for pattern in required_admin_patterns:
        assert pattern in admin_dom, f"Missing required admin pattern in /admin: '{pattern}'"
        print(f"  [PASS] /admin contains: '{pattern}'")

    print("\nALL ROUTE ISOLATION AND /ADMIN TELEMETRY CHECKS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    main()
