
def debug_log(msg):
    # Simple append to a file for debugging
    with open("debug_log.txt", "a") as f:
        f.write(msg + "\n")

