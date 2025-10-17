import gdb

logger = open("gdb_log.txt", "w")

def log(msg):
  """Log a message to gdb console.

  Parameters
  ----------
  msg : str
      The message to log.
  """
  log_msg = f"[LOGGER]{"" if (msg[0] == "[") else " "}{msg}\n"
  gdb.write(log_msg, gdb.STDOUT)
  gdb.flush()

  if logger:  
    logger.write(log_msg)
    logger.flush()

