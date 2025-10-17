import gdb

logger = None
def logging_on(log_filename):
  global logger
  logger = open(log_filename, "w")

def _gdb_logging_enabled():
  try:
    return gdb.parameter("logging enabled")
  except gdb.error:
    return False
  
def log(msg):
  """Log a message to gdb console.

  Parameters
  ----------
  msg : str
      The message to log.
  """
  gdb_logger = _gdb_logging_enabled()
  if gdb_logger or logger:
    log_msg = f"[LOGGER]{"" if (msg[0] == "[") else " "}{msg}\n"

  if gdb_logger:  
    gdb.write(log_msg, gdb.STDOUT)
    gdb.flush()

  if logger:  
    logger.write(log_msg)
    logger.flush()
