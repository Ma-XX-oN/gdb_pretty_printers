# GDB Pretty-Printer Framework

Framework to make a cohesive, easy-to-use pretty printer for gdb.  This uses
synthetic nodes to group data together into static, raw, and other views making
it easier to read and find information.

An object will look something like this in the Variable/Watch pane:
```
┰─ summary               # Raw or default view summary
┠┰ <Static>              # If static items in user type
┃┠─ static_item_0
┃┠─ ...
┃┖─ static_item_n
┠┰ <Raw> raw_summary     # If there are views and a default view was picked
┃┠─ raw_item_0
┃┠─ ...
┃┖─ raw_item_n
┠┰ <View 0> view_summary # If named, then the name of the view will be displayed
┃┠─ view_0_item_0
┃┠─ ...
┃┖─ view_0_item_n
┃...
┠┰ <View n> view_summary # If named, then the name of the view will be displayed
┃┠─ view_n_item_0
┃┠─ ...
┃┖─ view_n_item_n
┠─ item0 = value0        # either the raw or default view items at top level
┠┰ item1 item_summary
┃┠─ sub_item_0
┃┠─ ...
┃┖─ sub_item_n
┃...
```

NOTES:
------
- Summaries don't have to be specified in which case they'll be blank.
- `n` represents some value and doesn't mean that they are all the same `n`.

## What Makes This "Better"?
1. Synthetic nodes allow for grouping of related information together for
   different views of the same object.
2. Easier to setup.
3. Exposes ability to use older viewers if wanted.

## Setup for VSCode:
Create a pretty printer file:

### user_printers.py
```
import gdb
from gdb_printers import add_printer, summary
# There is only 1 pretty print file entry point so add any other imports of any
# other printers here.

add_printer("ColorRGBA", {
  "summary": summary(named=True, show_type=True), # summary for raw view
  # "default_view": "Alpha",   # optional default view if not raw
  "views": [
    {
      "name": "Alpha",
      "summary": summary(named=True, show_type=False), # summary for Alpha view
      "nodes": (
        'red',   lambda v: v['r'],
        'green', lambda v: v['g'],
        'blue',  lambda v: v['b']
      )
    },
    ...
  ]
})
```
To enable, merge this to the .vscode/launch.json file:
```
{
  "configurations": [
    {
      "setupCommands": [
        // vv gdb pretty printers vv

        // If not already there
        {
            "description": "Enable pretty-printing for gdb",
            "text": "-enable-pretty-printing",
            "ignoreFailures": true
        },
        {
          "description": "To be OS agnostic, change the environment's cwd",
          "text": "-environment-cd ${workspaceFolder}"
        },
        {
          "description": "ws_dir = ${workspaceFolder}",
          "text": "-interpreter-exec console \"python from pathlib import Path; ws_dir = Path.cwd()\""
        },
        {
          "description": "gdb_dirs = ws_dir/${config:gdb.prettyPrinter.dirs} and add them to import search path.",
          "text": "-interpreter-exec console \"python gdb_dirs = r'${config:gdb.prettyPrinter.dirs}'.split(','); import sys, os; sys.path[0:0] = [ str(ws_dir/gdb_dir).replace(chr(92), os.sep).replace('/', os.sep) for gdb_dir in gdb_dirs ]\""
        },

        // VVV LOCAL LOGGING VVV
        {
          "description": "Turn on external gdb logger for pretty printers (LPP)",
          "text": "-interpreter-exec console \"python import gdb_logger as LPP; LPP.logging_on(r'${config:gdb.prettyPrinter.log_pp}')\""
        },
        {
            "description": "Configure logging",
            "text": "-interpreter-exec console \"set logging overwrite on\"",
            "ignoreFailures": true
        },
        {
            "description": "Set log file",
            "text": "-interpreter-exec console \"set logging file ${config:gdb.prettyPrinter.log}\"",
            "ignoreFailures": true
        },
        {
            "description": "Enable logging",
            "text": "-interpreter-exec console \"set logging ${config:gdb.prettyPrinter.logOn}\"",
            "ignoreFailures": true
        },
        // ^^^ LOCAL LOGGING
        
        {
          "description": "Import pretty printers (PP)",
          "text": "-interpreter-exec console \"python import ${config:gdb.prettyPrinter.rootModule} as PP\""
        },

        // ^^ gdb pretty printers ^^
      ],
      // Additional logging if needed under "configurations" only outputs to DEBUG CONSOLE
      // "logging": { "engineLogging": true, "trace": true, "traceResponse": true }
    }
  ]
}
```

Then merge this into the .vscode/settings.json file.  If no file exists, make
one.
```
{
  "gdb": {
    "prettyPrinter": {
      // Relative to workspace root.  Should contain the directory for
      // gdb_printers.py and directories for any other printers.
      "dirs": [ "gdb" ],
      "rootModule": "user_printers", // Change to point at correct root pretty print module to import.
      // Stored in the workspace root.  Shows all gdb commands and pretty
      // printer logging.
      "log": "gdb.log",
      "logOn": "off", //"on",  // Valid values are on/off
      // Stored in the workspace root.  Shows only pretty printer logging.
      "log_pp": "", //"gdb_pp.log",  // If empty string then no logging.
    }
  }
}
```

Note: It'll look greyed out in VSCode.  Don't worry, it'll still work.

## Example:

### test.cpp
```
#include<stdint.h>
#include<iostream>
#include<vector>

struct mystruct {
  int x;
  double y;
  static int z;
};
int mystruct::z = 42;

struct ColorRGBA : public mystruct {
  ColorRGBA(uint8_t red, uint8_t green, uint8_t blue, uint8_t alpha)
  : r(red), g(green), b(blue), a(alpha)
  {
    x = 5;
    y = 3.14;
  }
  uint8_t r, g, b, a;
  std::vector<int> arrs = {1,2,3,4,5};

  // Static data members (appear automatically in a "<static>" section)
  static int instance_count;
  static const char* profile;
};

int ColorRGBA::instance_count = 3;
const char* ColorRGBA::profile = "hello";

int main() {
  ColorRGBA c{ 34, 139, 34, 255 };
  std::cout << "ColorRGBA instance at " << &c << "\n";
  return 0;
}
```

### user_printers.py
```
# Need to change gdb.prettyPrinter.rootModule in settings.json file to point at
# this file.
import gdb
from gdb_printers import add_printer, summary

# GDB pretty-printer example for ColorRGBA
class ComponentsNode:
  "Synthetic node for RGB components"
  def __init__(self, val):
    # val is the original ColorRGBA, not a pointer
    self.val = val
  
  def children(self):
    yield 'red', self.val['r']
    yield 'green', self.val['g'] 
    yield 'blue', self.val['b']

  def to_string(self):
    return None

class StatisticsNode:
  "Synthetic node for computed values"
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
      },
      {
        "name": "Statistics",
        "summary": '',
        "node": StatisticsNode
      }
    ]
  }
)
```
Or without the classes, which might have been useful for a really complex
viewer:
```
add_printer("ColorRGBA", {
    "summary": summary(named=True, show_type=True), # summary for raw view
    # "default_view": "Alpha",
    "views": [
      {
        "name": "Components",
        "summary": summary(named=True, show_type=False),
        "nodes": (
          'red',   lambda v: v['r'],
          'green', lambda v: v['g'],
          'blue',  lambda v: v['b']
        )
      },
      {
        "name": "Alpha",
        "summary": summary(named=False, show_type=False),
        "nodes": (
          "raw",        lambda v: v['a'],
          "normalized", lambda v: gdb.Value(float(v['a']) / 255.0)
        ),
      },
      {
        "name": "Statistics",
        "summary": '',
        "nodes": (
          'brightness', lambda v: gdb.Value(float((int(v['r']) + int(v['g']) + int(v['b'])) / 3.0)),
          'opacity',    lambda v: gdb.Value(float(v['a']) / 255.0)
        )
      }
    ]
  }
)
```

## Things Left To Do:

Add chunked child enumeration for large containers (std::vector, std::map or even user containers that have a way to iterate over the items it contains) with size thresholds and paging.