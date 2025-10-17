import gdb
from gdb_printers import add_printer, summary

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

add_printer("ColorRGBA", {
    "summary": summary(named=True, show_type=True), # summary for raw view
    # "default_view": "Alpha",
    "views": [
      {
        "name": "Components",
        "summary": summary(named=True, show_type=False),
        "node": ComponentsNode
      },
      {
        "name": "Alpha",
        "summary": summary(named=False, show_type=False),
        "nodes": (
          "raw",        lambda v: v['a'],
          "normalized", lambda v: gdb.Value(float(v['a']) / 255.0)
        ),
        # "elements": lambda v: emit_chunked_elements(v.begin(), v.end())
      },
      {
        "name": "Statistics",
        "summary": '',
        "node": StatisticsNode
      }
    ]
  }
)
