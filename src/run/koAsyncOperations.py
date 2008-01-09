#!/usr/bin/python

# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
# 
# The contents of this file are subject to the Mozilla Public License
# Version 1.1 (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
# 
# Software distributed under the License is distributed on an "AS IS"
# basis, WITHOUT WARRANTY OF ANY KIND, either express or implied. See the
# License for the specific language governing rights and limitations
# under the License.
# 
# The Original Code is Komodo code.
# 
# The Initial Developer of the Original Code is ActiveState Software Inc.
# Portions created by ActiveState Software Inc are Copyright (C) 2000-2008
# ActiveState Software Inc. All Rights Reserved.
# 
# Contributor(s):
#   ActiveState Software Inc
# 
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
# 
# ***** END LICENSE BLOCK *****

from __future__ import with_statement
import threading
import logging

from xpcom import components, COMException, ServerException, nsError
from xpcom.server import WrapObject, UnwrapObject
from xpcom._xpcom import PROXY_SYNC, PROXY_ALWAYS, PROXY_ASYNC, getProxyForObject


#---- globals
log = logging.getLogger('koAsyncOperations')
#log.setLevel(logging.DEBUG)


class koAsyncService(object):
    _com_interfaces_ = components.interfaces.koIAsyncService
    _reg_desc_ = "Asynchronous commands service"
    _reg_clsid_ = "{17f2ae57-e130-401e-8ab4-deb1234e16cd}"
    _reg_contractid_ = "@activestate.com/koAsyncService;1"

    # Make some local variables for these xpcom constants
    RESULT_SUCCESSFUL = components.interfaces.koIAsyncCallback.RESULT_SUCCESSFUL
    RESULT_STOPPED = components.interfaces.koIAsyncCallback.RESULT_STOPPED
    RESULT_ERROR = components.interfaces.koIAsyncCallback.RESULT_ERROR

    STATUS_RUNNING = components.interfaces.koIAsyncOperation.STATUS_RUNNING
    STATUS_STOPPING = components.interfaces.koIAsyncOperation.STATUS_STOPPING

    def __init__(self):
        self._runningOperations = []
        self._lockedUris = {}
        self._lock = threading.Lock()
        # The testing mode variable is used when running python tests from
        # the command line, as the getProxyForObject results in an object
        # that never actually calls the callback function.
        self.__testing_mode = False

    ###
    # Non-XPCOM functions
    ###

    def setTestingMode(self, value):
        self.__testing_mode = value

    def __run(self, name, aOp, aOpCallback, lock_these_uris):
        # Add the operation to the list
        tracking_tuple = (name, aOp, aOpCallback, lock_these_uris)
        with self._lock:
            self._runningOperations.append(tracking_tuple)
            for uri in lock_these_uris:
                self._lockedUris[uri] = self._lockedUris.get(uri, 0) + 1
        try:
            # Run the operation
            if aOpCallback:
                try:
                    aOpCallback = getProxyForObject(1, components.interfaces.koIAsyncCallback,
                                                    aOpCallback, PROXY_SYNC | PROXY_ALWAYS)
                except COMException:
                    pass
            try:
                aOp.status = self.STATUS_RUNNING
                data = aOp.run()
                log.debug("operation run finished, return data: %r", data)
            except Exception, ex:
                if aOpCallback:
                    log.debug("operation run raised exception: %r", ex)
                    e = ServerException(nsError.NS_ERROR_FAILURE, unicode(ex))
                    aOpCallback.callback(self.RESULT_ERROR, [e])
            else:
                if aOpCallback:
                    if data is None: data = []
                    try:
                        #print "\n%s: making callback\n" % (name, )
                        if aOp.status == self.STATUS_STOPPING:
                            aOpCallback.callback(self.RESULT_STOPPED, data)
                        else:
                            aOpCallback.callback(self.RESULT_SUCCESSFUL, data)
                    except Exception, ex:
                        log.debug("callback for %r failed: %r", name, ex)
        finally:
            # Remove the operation from the list
            with self._lock:
                try:
                    self._runningOperations.remove(tracking_tuple)
                except ValueError:
                    log.exception("Tracking tuple was already removed")
                # Unlock the uri's
                for uri in lock_these_uris:
                    num_locked = self._lockedUris.get(uri)
                    if num_locked is not None:
                        if num_locked > 1:
                            # Other operations are also locking this uri
                            self._lockedUris[uri] = num_locked - 1
                        else:
                            # Remove the lock
                            self._lockedUris.pop(uri)
                # XXX - Link with file status service?

    ###
    # XPCOM functions
    ###

    # Return the list of running koIAsyncOperation's
    def getRunningOperations(self):
        with self._lock:
            return [x[1] for x in self._runningOperations]

    # The "aOp" koIAsyncOperation must be implemented in Python, otherwise this
    # call will raise an exception. If the aOpCallback is defined, this objects
    # "callback" method will automatically called when the koIAsyncOperation run
    # finishes, either through normal operation, cancellation or through an
    # unexpected error.
    def run(self, name, aOp, aOpCallback, lock_these_uris):
        # Test if we can unwrap the operation. This ensure's that aOp is a
        # Python object! If it is not, then an exception will be raised.
        UnwrapObject(aOp)
        log.debug("Running asynchronous command: %r", name)
        t = threading.Thread(name=name,
                             target=self.__run,
                             args=(name, aOp, aOpCallback, lock_these_uris))
        t.setDaemon(True)
        t.start()
