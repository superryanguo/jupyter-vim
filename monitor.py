"""
Monitor for IPython/Jupyter console commands run from Vim.

Usage:
    1. Run jupyter/ipython console
    2. Run python monitor.py
    3. Connect Vim to console kernel using IPython command
"""
from __future__ import print_function
from glob import glob
import ast
import os
import re
import sys
import six
try:
    from jupyter_client import KernelManager, find_connection_file
except ImportError:
    from IPython.kernel import KernelManager, find_connection_file
try:
    from Queue import Empty
except ImportError:
    from queue import Empty

try:
    from pygments import highlight
except ImportError:
    highlight = lambda code, *args: code
else:
    from pygments.lexers import PythonLexer, Python3Lexer
    from pygments.formatters import TerminalFormatter
    formatter = TerminalFormatter()
    lexer = Python3Lexer() if six.PY3 else PythonLexer()

colors = {k: i for i, k in enumerate([
    'black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'])}

#------------------------------------------------------------------------------ 
#        Function definitions
#------------------------------------------------------------------------------
def colorize(string, color, bold=False, bright=False):
    if isinstance(color, str):
        code = ''.join(('\033[', str(colors[color] + (90 if bright else 30))))
    else:
        code = '\033[38;5;%d' % color
    return ''.join((code, ';1' if bold else '', 'm', string, '\033[0m'))

def get_msgs():
    try:
        kc.iopub_channel.flush()
        return kc.iopub_channel.get_msgs()
    except AttributeError:
        msgs = []
        while True:
            try:
                msgs.append(kc.iopub_channel.get_msg(timeout=0.001))
            except Empty:
                return msgs

#------------------------------------------------------------------------------ 
#        Class definition
#------------------------------------------------------------------------------
class IPythonMonitor(object):

    def __init__(self):
        self.clients = set()
        self.execution_count_id = None
        self.last_msg_type = None  # Only set when text written to stdout
        self.last_execution_count = 0

    def print_prompt(self, start='In', color=28, num_color=46, count_offset=0):
        count = str(self.last_execution_count + count_offset)
        sys.stdout.write(colorize(start.rstrip() + ' [', color))
        sys.stdout.write(colorize(count, num_color, bold=True))
        sys.stdout.write(colorize(']: ', color))
        return '%s [%s]: ' % (start.strip(), count)

    def listen(self):
        while socket.recv():
            for msg in get_msgs():
                msg_type = msg['msg_type']

                if msg_type == 'shutdown_reply':
                    sys.exit(0)

                client = msg['parent_header'].get('session', '')
                if (client and msg_type in ('execute_input', 'pyin') and
                        msg['content']['code'] == '"_vim_client";_=_;__=__'):
                    self.clients.add(client)
                    continue
                if client not in self.clients:
                    continue

                getattr(self, msg_type, self.other)(msg)
                sys.stdout.flush()

    def pyin(self, msg):
        self.last_execution_count = msg['content']['execution_count']
        sys.stdout.write('\r')
        dots = ' ' * (len(self.print_prompt().rstrip()) - 1) + ': '
        code = highlight(msg['content']['code'], lexer, formatter)
        output = code.rstrip().replace('\n', '\n' + colorize(dots, 28))
        sys.stdout.write(output)
        self.execution_count_id = msg['parent_header']['msg_id']
        self.last_msg_type = msg['msg_type']

    def pyout(self, msg, prompt=True, spaces=''):
        if 'execution_count' in msg['content']:
            self.last_execution_count = msg['content']['execution_count']
            self.execution_count_id = msg['parent_header']['msg_id']
        output = msg['content']['data']['text/plain']
        if prompt:
            self.print_prompt('\nOut', 196, 196)
            sys.stdout.write(('\n' if '\n' in output else '') + output)
        else:
            sys.stdout.write(output)
        self.last_msg_type = msg['msg_type']

    def display_data(self, msg):
        sys.stdout.write('\n')
        self.pyout(msg, prompt=False)

    def pyerr(self, msg):
        for line in msg['content']['traceback']:
            sys.stdout.write('\n' + line)
        if self.last_msg_type not in ('execute_input', 'pyin'):
            self.print_prompt('\nIn')
        self.last_msg_type = msg['msg_type']

    def stream(self, msg):
        if self.last_msg_type not in ('pyerr', 'error', 'stream'):
            sys.stdout.write('\n')
        try:
            data = msg['content']['data']
        except KeyError:
            data = msg['content']['text']
        sys.stdout.write(colorize(data, 'cyan', bright=True))
        self.last_msg_type = msg['msg_type']

    def status(self, msg):
        if (msg['content']['execution_state'] == 'idle' and
                msg['parent_header']['msg_id'] == self.execution_count_id):
            self.print_prompt('\nIn', count_offset=1)
            self.execution_count_id = None

    def clear_output(self, msg):
        if self.last_msg_type in ('execute_input', 'pyin'):
            print('\n')
        print('\033[2K\r', file=sys.stdout, end='')

    def other(self, msg):
        print('msg_type = %s' % str(msg['msg_type']))
        print('msg = %s' % str(msg))

    execute_input = pyin
    execute_result = pyout
    error = pyerr

#------------------------------------------------------------------------------ 
#       Connect to the kernel
#------------------------------------------------------------------------------
connected = False
while not connected:
    # No need for old paths() function... find_connection_file guaranteed to
    # return a single filename with absolute path
    filename = find_connection_file('kernel*.json')

    # Create the kernel manager and connect a client
    km = KernelManager(connection_file=filename)
    km.load_connection_file()
    kc = km.client()
    kc.start_channels()

    # Get the right execution function given the version of KernelClient in use
    try:
        send = kc.execute
    except AttributeError:
        send = kc.shell_channel.execute

    # Update to newest channel name
    if not hasattr(kc, 'iopub_channel'):
        kc.iopub_channel = kc.sub_channel

    # Ping the kernel
    send('', silent=True)
    try:
        msg = kc.shell_channel.get_msg(timeout=1)
        connected = True
        # Set the socket on which to listen for messages
        socket = km.connect_iopub()
        print('IPython monitor connected successfully!')
        break
    # <C-c> or kill -SIGINT?
    except KeyboardInterrupt:
        sys.exit(0)
    # if msg is empty, just try again
    except (Empty, KeyError):
        continue
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        if not connected:
            kc.stop_channels()

#------------------------------------------------------------------------------ 
#       Set stdout
#------------------------------------------------------------------------------
if len(sys.argv) > 1:
    # Set stdout to arbitrary file descriptor given as script argument
    #   $ python monitor.py monitor_log.txt &
    term = open(sys.argv[1], 'w')
    sys.stdout = term
else:
    # Set stdout to terminal in which kernel is running
    msg_id = send('import os as _os; _tty = _os.ttyname(1)', silent=True,
                  user_expressions=dict(_tty='_tty'))
    while True:
        try:
            msg = kc.shell_channel.get_msg(timeout=1.0)
            if msg['parent_header']['msg_id'] == msg_id:
                fd = ast.literal_eval(msg['content']['user_expressions']
                        ['_tty']['data']['text/plain'])
                # print("setting sys.stdout to file descriptor: {}".format(fd))
                sys.stdout = open(fd, 'w+')
                break
        except Empty:
            continue

#------------------------------------------------------------------------------ 
#        Create and run the monitor
#------------------------------------------------------------------------------
monitor = IPythonMonitor()
monitor.listen()

#==============================================================================
#==============================================================================