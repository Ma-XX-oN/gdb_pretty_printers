import gdb

def log(msg):
    """Log a message to gdb console.

    Parameters
    ----------
    msg : str
        The message to log.
    """
    gdb.write(f"[LOGGER]{"" if (msg[0] == "[") else " "}{msg}\n", gdb.STDOUT)

