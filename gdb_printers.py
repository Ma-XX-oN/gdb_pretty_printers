import gdb

from gdb_logger import log
from gdb_synthetic_nodes import make_enums_tag, extract_enums_tag, recover_value

# GDB pretty-printer example for ColorRGBA

class ComponentsNode:
  """Synthetic node for RGB components"""
  def __init__(self, val):
    # val is the original ColorRGBA, not a pointer
    self.val = val
  
  def children(self):
    yield 'red', self.val['r']
    yield 'green', self.val['g'] 
    yield 'blue', self.val['b']

  def to_string(self):
    return None

class AlphaNode:
  """Synthetic node for alpha channel"""
  def __init__(self, val):
    self.val = val
  
  def children(self):
    yield 'raw', self.val['a']
    yield 'normalized', gdb.Value(float(self.val['a']) / 255.0)

  def to_string(self):
    return None

class StatisticsNode:
  """Synthetic node for computed values"""
  def __init__(self, val):
    self.val = val
  
  def children(self):
    brightness = (int(self.val['r']) + int(self.val['g']) + int(self.val['b'])) / 3.0
    yield 'brightness', gdb.Value(float(brightness))
    yield 'opacity', gdb.Value(float(self.val['a']) / 255.0)

  def to_string(self):
    return None

class ColorRGBAPrinter:
  def __init__(self, val):
    self.val = val

  def children(self):
    yield 'Components', make_enums_tag(self.val, (0,))
    yield 'Alpha', make_enums_tag(self.val, (1,))
    yield 'Statistics', make_enums_tag(self.val, (2,))

  def to_string(self):
    return "ColorRGBA"

def lookup_type(val):
  log(f"type: {val.type}\n")
  type_str = str(val.type)
  
  # Match base type
  if type_str == 'ColorRGBA':
    return ColorRGBAPrinter(val)
  
  # Match synthetic node tags by looking for pointer pattern
  if '***' in type_str:
    enums = extract_enums_tag(val)
    log(f"enums: {enums}\n")
    if enums[0] == 0:
      return ComponentsNode(recover_value(val))
    elif enums[0] == 1:
      return AlphaNode(recover_value(val))
    elif enums[0] == 2:
      return StatisticsNode(recover_value(val))
  
  return None

gdb.pretty_printers.append(lookup_type)