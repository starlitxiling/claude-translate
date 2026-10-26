import json
import os
import sys

event = json.load(sys.stdin)
descriptor = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
try:
    os.write(descriptor, (json.dumps(event) + "\n").encode())
finally:
    os.close(descriptor)
print("{}")
