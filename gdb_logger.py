import gdb

logger = None
def logging_on(log_filename):
  if log_filename == '':
    return
  global logger
  logger = open(log_filename, "w")

def log(msg):
  """Log a message to gdb console.

  Parameters
  ----------
  msg : str
      The message to log.
  """
  # gdb_logger = _gdb_logging_enabled()
  log_msg = f"[LOGGER]{"" if (msg[0] == "[") else " "}{msg}\n"

  gdb.write(log_msg, gdb.STDOUT)
  gdb.flush()

  if logger:  
    logger.write(log_msg)
    logger.flush()
