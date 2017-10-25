#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""An iControl client library.

See the documentation for the L{BIGIP} class for usage examples.
"""
try:
    # Python 2.x
    import httplib
    from urllib2 import URLError
    from httplib import BadStatusLine
    from urllib2 import build_opener
    from urllib2 import HTTPBasicAuthHandler
    from urllib2 import HTTPSHandler
except ImportError:
     # Python 3.x
     import http.client as httplib
     from urllib.error import URLError
     from http.client import BadStatusLine
     from urllib.request import build_opener
     from urllib.request import HTTPBasicAuthHandler
     from urllib.request import HTTPSHandler

from six import PY2
import logging
import os
import re
import ssl
from xml.sax import SAXParseException

import suds.client
from suds.cache import ObjectCache
from suds.sudsobject import Object as SudsObject
from suds.client import Client
from suds.xsd.doctor import ImportDoctor, Import
from suds.transport import TransportError
from suds.transport.https import HttpAuthenticated
from suds import WebFault, TypeNotFound, MethodNotFound as _MethodNotFound

import six

__version__ = '1.0.6'


# We need to monkey-patch the Client's ObjectCache due to a suds bug:
# https://fedorahosted.org/suds/ticket/376
suds.client.ObjectCache = lambda **kwargs: None

# We need to add support for SSL Contexts for Python 2.7.9+
class HTTPSHandlerNoVerify(HTTPSHandler):
    def __init__(self, *args, **kwargs):
        try:
            kwargs['context'] = ssl._create_unverified_context()
        except AttributeError:
            # Python prior to 2.7.9 doesn't have default-enabled certificate
            # verification
            pass

        HTTPSHandler.__init__(self, *args, **kwargs)

class HTTPSTransportNoVerify(HttpAuthenticated):
    def u2handlers(self):
        handlers = HttpAuthenticated.u2handlers(self)
        handlers.append(HTTPSHandlerNoVerify())
        return handlers

log = logging.getLogger('bigsuds')


class OperationFailed(Exception):
    """Base class for bigsuds exceptions."""

class ServerError(OperationFailed, WebFault):
    """Raised when the BIGIP returns an error via the iControl interface."""

class ConnectionError(OperationFailed):
    """Raised when the connection to the BIGIP fails."""

class ParseError(OperationFailed):
    """Raised when parsing data from the BIGIP as a soap message fails.

    This is also raised when an invalid iControl namespace
    is looked up on the BIGIP (e.g. <bigip>.LocalLB.Bad).
    """

class MethodNotFound(OperationFailed, _MethodNotFound):
    """Raised when a particular iControl method does not exist."""

class ArgumentError(OperationFailed):
    """Raised when too many arguments or incorrect keyword arguments
    are passed to an iControl method."""


class BIGIP(object):
    """This class exposes the BIGIP's iControl interface.

    Example usage:
        >>> b = BIGIP('bigip-hostname')
        >>> print b.LocalLB.Pool.get_list()
        ['/Common/test_pool']
        >>> b.LocalLB.Pool.add_member(['/Common/test_pool'], \
                [[{'address': '10.10.10.10', 'port': 20030}]])
        >>> print b.LocalLB.Pool.get_member(['/Common/test_pool'])
        [[{'port': 20020, 'address': '10.10.10.10'},
          {'port': 20030, 'address': '10.10.10.10'}]]

    Some notes on Exceptions:
     * The looking up of iControl namespaces on the L{BIGIP} instance can raise
       L{ParseError} and L{ServerError}.
     * The looking up of an iControl method can raise L{MethodNotFound}.
     * Calling an iControl method can raise L{ServerError} when the BIGIP
       reports an error via iControl, L{ConnectionError}, or L{MethodNotFound},
       or L{ParseError} when the BIGIP return non-SOAP data, or
       L{ArgumentError} when too many arguments are passed or invalid
       keyword arguments are passed.
     * All of these exceptions derive from L{OperationFailed}.
    """
    def __init__(self, hostname, username='admin', password='admin',
                 debug=False, cachedir=None, verify=False, timeout=90,
                 port=443):
        """init

        @param hostname: The IP address or hostname of the BIGIP.
        @param username: The admin username on the BIGIP.
        @param password: The admin password on the BIGIP.
        @param debug: When True sets up additional interactive features
            like the ability to introspect/tab-complete the list of method
            names.
        @param cachedir: The directory to cache wsdls in. None indicates
            that caching should be disabled.
        @param verify: When True, performs SSL certificate validation in
            Python / urllib2 versions that support it (v2.7.9 and newer)
        @param timeout: The time (in seconds) to wait before timing out
            the connection to the URL
        """
        self._hostname = hostname
        self._port = port
        self._username = username
        self._password = password
        self._debug = debug
        self._cachedir = cachedir
        self._verify = verify
        self._timeout = timeout
        if debug:
            self._instantiate_namespaces()

    def with_session_id(self, session_id=None):
        """Returns a new instance of L{BIGIP} that uses a unique session id.

        @param session_id: The integer session id to use. If None, a new
            session id will be requested from the BIGIP.
        @return: A new instance of L{BIGIP}. All iControl calls made through
            this new instance will use the unique session id. All calls made
            through the L{BIGIP} that with_session_id() was called on will
            continue to use that instances session id (or no session id if
            it did not have one).

        @raise: MethodNotFound: When no session_id is specified and the BIGIP
            does not support sessions. Sessions are new in 11.0.0.
        @raise: OperationFaled: When getting the session_id from the BIGIP
            fails for some other reason.
        """
        if session_id is None:
            session_id = self.System.Session.get_session_identifier()
        return _BIGIPSession(self._hostname, session_id, self._username,
                             self._password, self._debug, self._cachedir)

    def __getattr__(self, attr):
        if attr.startswith('__'):
            return getattr(super(BIGIP, self), attr)
        if '_' in attr:
            # Backwards compatibility with pycontrol:
            first, second = attr.split('_', 1)
            return getattr(getattr(self, first), second)
        ns = _Namespace(attr, self._create_client)
        setattr(self, attr, ns)
        return ns

    def _create_client(self, wsdl_name):
        try:
            client = get_client(self._hostname, wsdl_name, self._username,
                                self._password, self._cachedir, self._verify,
                                self._timeout,self._port)
        except SAXParseException as e:
            raise ParseError('%s\nFailed to parse wsdl. Is "%s" a valid '
                    'namespace?' % (e, wsdl_name))
        # One situation that raises TransportError is when credentials are bad.
        except (URLError, TransportError) as e:
            raise ConnectionError(str(e))
        return self._create_client_wrapper(client, wsdl_name)

    def _create_client_wrapper(self, client, wsdl_name):
        return _ClientWrapper(client,
            self._arg_processor_factory,
            _NativeResultProcessor,
            wsdl_name,
            self._debug)

    def _arg_processor_factory(self, client, method):
        return _DefaultArgProcessor(method, client.factory)

    def _instantiate_namespaces(self):
        wsdl_hierarchy = get_wsdls(self._hostname, self._username,
                                   self._password, self._verify,
                                   self._timeout, self._port)
        for namespace, attr_list in six.iteritems(wsdl_hierarchy):
            ns = getattr(self, namespace)
            ns.set_attr_list(attr_list)

class Transaction(object):
    """This class is a context manager for iControl transactions.

    Upon successful exit of the with statement, the transaction will be
    submitted, otherwise it will be rolled back.

    NOTE: This feature was added to BIGIP in version 11.0.0.

    Example:
    > bigip = BIGIP(<args>)
    > with Transaction(bigip):
    >     <perform actions inside a transaction>

    Example which creates a new session id for the transaction:
    > bigip = BIGIP(<args>)
    > with Transaction(bigip.use_session_id()) as bigip:
    >     <perform actions inside a transaction>
    """
    def __init__(self, bigip):
        self.bigip = bigip

    def __enter__(self):
        self.bigip.System.Session.start_transaction()
        return self.bigip

    def __exit__(self, excy_type, exc_value, exc_tb):
        if exc_tb is None:
            self.bigip.System.Session.submit_transaction()
        else:
            try:
                self.bigip.System.Session.rollback_transaction()
            # Ignore ServerError. This happens if the transaction is already
            # timed out. We don't want to ignore other errors, like
            # ConnectionErrors.
            except ServerError:
                pass


def get_client(hostname, wsdl_name, username='admin', password='admin',
               cachedir=None, verify=False, timeout=90, port=443):
    """Returns and instance of suds.client.Client.

    A separate client is used for each iControl WSDL/Namespace (e.g.
    "LocalLB.Pool").

    This function allows any suds exceptions to propagate up to the caller.

    @param hostname: The IP address or hostname of the BIGIP.
    @param wsdl_name: The iControl namespace (e.g. "LocalLB.Pool")
    @param username: The admin username on the BIGIP.
    @param password: The admin password on the BIGIP.
    @param cachedir: The directory to cache wsdls in. None indicates
        that caching should be disabled.
    @param verify: When True, performs SSL certificate validation in
        Python / urllib2 versions that support it (v2.7.9 and newer)
    @param timeout: The time to wait (in seconds) before timing out
        the connection to the URL
    """
    url = 'https://%s:%s/iControl/iControlPortal.cgi?WSDL=%s' % (
            hostname, port, wsdl_name)
    imp = Import('http://schemas.xmlsoap.org/soap/encoding/')
    imp.filter.add('urn:iControl')

    if cachedir is not None:
        cachedir = ObjectCache(location=os.path.expanduser(cachedir), days=1)

    doctor = ImportDoctor(imp)
    if verify:
        client = Client(url, doctor=doctor, username=username, password=password,
                        cache=cachedir, timeout=timeout)
    else:
        transport = HTTPSTransportNoVerify(username=username,
                                           password=password, timeout=timeout)
        client = Client(url, doctor=doctor, username=username, password=password,
                        cache=cachedir, transport=transport, timeout=timeout)

    # Without this, subsequent requests will use the actual hostname of the
    # BIGIP, which is often times invalid.
    client.set_options(location=url.split('?')[0])
    client.factory.separator('_')
    return client


def get_wsdls(hostname, username='admin', password='admin', verify=False,
              timeout=90, port=443):
    """Returns the set of all available WSDLs on this server

    Used for providing introspection into the available namespaces and WSDLs
    dynamically (e.g. when using iPython)

    @param hostname: The IP address or hostname of the BIGIP.
    @param username: The admin username on the BIGIP.
    @param password: The admin password on the BIGIP.
    @param verify: When True, performs SSL certificate validation in
        Python / urllib2 versions that support it (v2.7.9 and newer)
    @param timeout: The time to wait (in seconds) before timing out the connection
        to the URL
    """
    url = 'https://%s:%s/iControl/iControlPortal.cgi' % (hostname, port)
    regex = re.compile(r'/iControl/iControlPortal.cgi\?WSDL=([^"]+)"')

    auth_handler = HTTPBasicAuthHandler()
    # 10.1.0 has a realm of "BIG-IP"
    auth_handler.add_password(uri='https://%s:%s/' % (hostname, port),
                              user=username, passwd=password, realm="BIG-IP")
    # 11.3.0 has a realm of "BIG-\IP". I'm not sure exactly when it changed.
    auth_handler.add_password(uri='https://%s:%s/' % (hostname, port),
                              user=username, passwd=password, realm="BIG\-IP")
    if verify:
        opener = build_opener(auth_handler)
    else:
        opener = build_opener(auth_handler, HTTPSHandlerNoVerify)
    try:
        result = opener.open(url, timeout=timeout)
    except URLError as e:
        raise ConnectionError(str(e))

    wsdls = {}
    for line in result.readlines():
        result = regex.search(line)
        if result:
            namespace, rest = result.groups()[0].split(".", 1)
            if namespace not in wsdls:
                wsdls[namespace] = []
            wsdls[namespace].append(rest)
    return wsdls


class _BIGIPSession(BIGIP):
    def __init__(self, hostname, session_id, username='admin', password='admin',
                 debug=False, cachedir=None):
        super(_BIGIPSession, self).__init__(hostname, username=username,
              password=password, debug=debug, cachedir=cachedir)
        self._headers = {'X-iControl-Session': str(session_id)}

    def _create_client_wrapper(self, client, wsdl_name):
        client.set_options(headers=self._headers)
        return super(_BIGIPSession, self)._create_client_wrapper(client, wsdl_name)


class _Namespace(object):
    """Represents a top level iControl namespace.

    Examples of this are "LocalLB", "System", etc.

    The purpose of this class is to store context allowing iControl clients
    to be looked up using only the remaining part of the namespace.
    Example:
        <LocalLB namespace>.Pool returns the iControl client for "LocalLB.Pool"
    """
    def __init__(self, name, client_creator):
        """init

        @param name: The high-level namespace (e.g "LocalLB").
        @param client_creator: A function that will be passed the full
            namespace string (e.g. "LocalLB.Pool") and should return
            some type of iControl client.
        """
        self._name = name
        self._client_creator = client_creator
        self._attrs = []

    def __dir__(self):
        return sorted(set(dir(type(self)) + list(self.__dict__) +
                          self._attrs))

    def __getattr__(self, attr):
        if attr.startswith('__'):
            return getattr(super(_Namespace, self), attr)
        client = self._client_creator('%s.%s' % (self._name, attr))
        setattr(self, attr, client)
        return client

    def set_attr_list(self, attr_list):
        self._attrs = attr_list


class _ClientWrapper(object):
    """A wrapper class that abstracts/extends the suds client API.
    """
    def __init__(self, client, arg_processor_factory, result_processor_factory,
                 wsdl_name, debug=False):
        """init

        @param client: An instance of suds.client.Client.
        @param arg_processor_factory: This will be called to create processors
            for arguments before they are passed to suds methods. This callable
                will be passed the suds method and factory and should return an
            instance of L{_ArgProcessor}.
        @param result_processor_factory: This will be called to create
            processors for results returned from suds methods. This callable
            will be passed no arguments and should return an instance of
            L{_ResultProcessor}.
        """
        self._client = client
        self._arg_factory = arg_processor_factory
        self._result_factory = result_processor_factory
        self._wsdl_name = wsdl_name
        self._usage = {}

        # This populates self.__dict__. Helpful for tab completion.
        # I'm not sure if this slows things down much. Maybe we should just
        # always do it.
        if debug:
            # Extract the documentation from the WSDL (before populating
            # self.__dict__)
            binding_el = client.wsdl.services[0].ports[0].binding[0]
            for op in binding_el.getChildren("operation"):
                usage = None
                doc = op.getChild("documentation")
                if doc is not None:
                    usage = doc.getText().strip()
                self._usage[op.get("name")] = usage

            for method in client.sd[0].ports[0][1]:
                getattr(self, method[0])

    def __getattr__(self, attr):
        # Looks up the corresponding suds method and returns a wrapped version.
        try:
            method = getattr(self._client.service, attr)
        except _MethodNotFound as e:
            e.__class__ = MethodNotFound
            raise

        wrapper = _wrap_method(method,
                self._wsdl_name,
                self._arg_factory(self._client, method),
                self._result_factory(),
                attr in self._usage and self._usage[attr] or None)
        setattr(self, attr, wrapper)
        return wrapper

    def __str__(self):
        # The suds clients strings contain the entire soap API. This is really
        # useful, so lets expose it.
        return str(self._client)


def _wrap_method(method, wsdl_name, arg_processor, result_processor, usage):
    """
    This function wraps a suds method and returns a new function which
    provides argument/result processing.

    Each time a method is called, the incoming args will be passed to the
    specified arg_processor before being passed to the suds method.

    The return value from the underlying suds method will be passed to the
    specified result_processor prior to being returned to the caller.

    @param method: A suds method (can be obtained via
        client.service.<method_name>).
    @param arg_processor: An instance of L{_ArgProcessor}.
    @param result_processor: An instance of L{_ResultProcessor}.

    """

    icontrol_sig = "iControl signature: %s" % _method_string(method)

    if usage:
        usage += "\n\n%s" % icontrol_sig
    else:
        usage = "Wrapper for %s.%s\n\n%s" % (
            wsdl_name, method.method.name, icontrol_sig)

    def wrapped_method(*args, **kwargs):
        log.debug('Executing iControl method: %s.%s(%s, %s)',
                  wsdl_name, method.method.name, args, kwargs)
        args, kwargs = arg_processor.process(args, kwargs)
        # This exception wrapping is purely for pycontrol compatability.
        # Maybe we want to make this optional and put it in a separate class?
        try:
            result = method(*args, **kwargs)
        except AttributeError:
            # Oddly, this seems to happen when the wrong password is used.
            raise ConnectionError('iControl call failed, possibly invalid '
                    'credentials.')
        except _MethodNotFound as e:
            e.__class__ = MethodNotFound
            raise
        except WebFault as e:
            e.__class__ = ServerError
            raise
        except URLError as e:
            raise ConnectionError('URLError: %s' % str(e))
        except BadStatusLine as e:
            raise ConnectionError('BadStatusLine: %s' %  e)
        except SAXParseException as e:
            raise ParseError("Failed to parse the BIGIP's response. This "
                "was likely caused by a 500 error message.")
        return result_processor.process(result)

    wrapped_method.__doc__ = usage
    wrapped_method.__name__ = str(method.method.name)
    # It's occasionally convenient to be able to grab the suds object directly
    wrapped_method._method = method
    return wrapped_method


class _ArgProcessor(object):
    """Base class for suds argument processors."""

    def process(self, args, kwargs):
        """This method is passed the user-specified args and kwargs.

        @param args: The user specified positional arguements.
        @param kwargs: The user specified keyword arguements.
        @return: A tuple of (args, kwargs).
        """
        raise NotImplementedError('process')


class _DefaultArgProcessor(_ArgProcessor):

    def __init__(self, method, factory):
        self._factory = factory
        self._method = method
        self._argspec = self._make_argspec(method)

    def _make_argspec(self, method):
        # Returns a list of tuples indicating the arg names and types.
        # E.g., [('pool_names', 'Common.StringSequence')]
        spec = []
        for part in method.method.soap.input.body.parts:
            spec.append((part.name, part.type[0]))
        return spec

    def process(self, args, kwargs):
        return (self._process_args(args), self._process_kwargs(kwargs))

    def _process_args(self, args):
        newargs = []
        for i, arg in enumerate(args):
            try:
                newargs.append(self._process_arg(self._argspec[i][1], arg))
            except IndexError:
                raise ArgumentError(
                    'Too many arguments passed to method: %s' % (
                        _method_string(self._method)))
        return newargs

    def _process_kwargs(self, kwargs):
        newkwargs = {}
        for name, value in six.iteritems(kwargs):
            try:
                argtype = [x[1] for x in self._argspec if x[0] == name][0]
                newkwargs[name] = self._process_arg(argtype, value)
            except IndexError:
                raise ArgumentError(
                    'Invalid keyword argument "%s" passed to method: %s' % (
                        name, _method_string(self._method)))
        return newkwargs

    def _process_arg(self, arg_type, value):
        if isinstance(value, SudsObject):
            # If the user explicitly created suds objects to pass in,
            # we don't want to mess with them.
            return value

        if '.' not in arg_type and ':' not in arg_type:
            # These are not iControl namespace types, they are part of:
            # ns0 = "http://schemas.xmlsoap.org/soap/encoding/"
            # From what I can tell, we don't need to send these to the factory.
            # Sending them to the factory as-is actually fails to resolve, the
            # type names would need the "ns0:" qualifier. Some examples of
            # these types are: ns0:string, ns0:long, ns0:unsignedInt.
            return value

        try:
            obj = self._factory.create(arg_type)
        except TypeNotFound:
            log.error('Failed to create type: %s', arg_type)
            return value

        if isinstance(value, dict):
            for name, value in six.iteritems(value):
                # The new object we created has the type of each attribute
                # accessible via the attribute's class name.
                try:
                    class_name = getattr(obj, name).__class__.__name__
                except AttributeError:
                    valid_attrs = ', '.join([x[0] for x in obj])
                    raise ArgumentError(
                        '"%s" is not a valid attribute for %s, '
                        'expecting: %s' % (name, obj.__class__.__name__,
                                           valid_attrs))
                setattr(obj, name, self._process_arg(class_name, value))
            return obj

        array_type = self._array_type(obj)
        if array_type is not None:
            # This is a common mistake. We might as well catch it here.
            if isinstance(value, six.string_types):
                raise ArgumentError(
                    '%s needs an iterable, but was specified as a string: '
                    '"%s"' % (obj.__class__.__name__, value))
            obj.items = [self._process_arg(array_type, x) for x in value]
            return obj

        # If this object doesn't have any attributes, then we know it's not
        # a complex type or enum type. We'll want to skip the next validation
        # step.
        if not obj:
            return value

        # The passed in value doesn't belong to an array type and wasn't a
        # complex type (no dictionary received). At this point we know that
        # the object type has attributes associated with it. It's likely
        # an enum, but could be an incorrect argument to a complex type (e.g.
        # the user specified some other type when a dictionary is expected).
        # Either way, this error is more helpful than what the BIGIP provides.
        if value not in obj:
            valid_values = ', '.join([x[0] for x in obj])
            raise ArgumentError('"%s" is not a valid value for %s, expecting: '
                                '%s' % (value, obj.__class__.__name__,
                                        valid_values))
        return value

    def _array_type(self, obj):
        # Determines if the specified type is an array.
        # If so, the type name of the elements is returned. Otherwise None
        # is returned.
        try:
            attributes = obj.__metadata__.sxtype.attributes()
        except AttributeError:
            return None
        # The type contained in the array is in one of the attributes.
        # According to a suds docstring, the "aty" is the "soap-enc:arrayType".
        # We need to find the attribute which has it.
        for each in attributes:
            if each[0].name == 'arrayType':
                try:
                    return each[0].aty[0]
                except AttributeError:
                    pass
        return None


class _ResultProcessor(object):
    """Base class for suds result processors."""

    def process(self, value):
        """Processes the suds return value for the caller.

        @param value: The return value from a suds method.
        @return: The processed value.
        """
        raise NotImplementedError('process')


class _NativeResultProcessor(_ResultProcessor):
    def process(self, value):
        return self._convert_to_native_type(value)

    def _convert_to_native_type(self, value):
        if isinstance(value, list):
            return [self._convert_to_native_type(x) for x in value]
        elif isinstance(value, SudsObject):
            d = {}
            for attr_name, attr_value in value:
                d[attr_name] = self._convert_to_native_type(attr_value)
            return d
        elif isinstance(value, six.string_types):
            # This handles suds.sax.text.Text as well, as it derives from
            # unicode.
            if PY2:
                return str(value.encode('utf-8'))
            else:
                return str(value)
        elif isinstance(value, six.integer_types):
            return int(value)
        return value


def _method_string(method):
    parts = []
    for part in method.method.soap.input.body.parts:
        parts.append("%s %s" % (part.type[0], part.name))
    return "%s(%s)" % (method.method.name, ', '.join(parts))
