'''
accept input from command line or through the web and
return the result.
'''

import urllib.request, urllib.parse, urllib.error, os.path, traceback
from indigo import Indigo, IndigoException

from . import common, options, molecule

class HelpError(common.MCFError):
    pass

class Processor(object):
    '''
    parses input and invokes backend, returns result
    '''
    def __init__(self, rawargs, data, formfields, progname, webform, rpc):
        self.rawargs = rawargs
        self.data = data
        self.formfields = formfields

        # if the user renames the script file or the
        # web client, use their new names
        self.progname = os.path.split(progname)[-1]

        # flags that indicate origin of input
        self.webform = webform
        self.rpc = rpc

        self.optionparser = options.getParser()
        self.options = dict(common.settings)

        # data obtained from the proper source go here
        self.data_string = None


    def version_text(self):
        '''
        print the program version
        '''
        return common.version_text(progname=self.progname)


    def help_text(self):
        '''
        error messages for the command line interface.
        '''
        return common.help_text(progname=self.progname)


    def parseInputCli(self):
        '''
        parse input that came through the command line (locally or rpc)
        return success flag and either error message or data
        '''
        # catch empty input
        if not self.rawargs and not self.data:
            ht = self.help_text()

            raise HelpError(ht)

        # parse options and arguments
        try:
            parsed_options, datalist = self.optionparser.process_cli(self.rawargs)
        except Exception as msg:
            if str(msg).endswith('not recognized'): # getopt error
                msg = str(msg) + \
                      ". Try %s --help to see a list of available options." % self.progname
            raise HelpError(msg)

        # if we get here, we have parsed options and a possibly empty datalist
        self.options.update(parsed_options)

        # before we go on to check on the data, we will satisfy help requests,
        # which we treat like an error
        if self.options['help']:
            raise HelpError(self.help_text())
        elif self.options['version']:
            raise HelpError(self.version_text())

        if self.data is not None:
            datalist.append(self.data)

        # at this point, we should have reached the same state
        # by rpc and local invocation

        if len(datalist) != 1:
            if not datalist:
                raise common.MCFError("No input data supplied")
            raise common.MCFError("Please give only one file or data string as input")

        data = datalist[0]

        if not self.rpc and self.options['input'] == 'file':
            try:
                data = open(data).read()
            except IOError:
                raise common.MCFError("Can't read file %s" % data)

        self.data_string = data


    def parseInputWeb(self):
        '''
        parse options and provide data provided through the web form
        '''
        parsed_options, warnings = self.optionparser.process_form_fields(self.formfields)

        if warnings:
            raise common.MCFError('<br/>\n'.join(warnings))

        # no warnings ...
        self.options.update(parsed_options)
        self.data_string = self.data


    def process(self):
        '''
        process input from both web form and CLI
        '''
        if not self.webform:
            self.parseInputCli()
        else:
            self.parseInputWeb()
        # let toolkit parse the molecule, and process it
        tkmol = self.parseMolecule()

        # we now know how to deal with orphan atoms
        #atoms, bonds = tkmol.countAtoms(), tkmol.countBonds()
        #if atoms <= 1 or bonds == 0:
            #raise common.MCFError, "Input contains no bonds---can't render structure"

        mol = molecule.Molecule(self.options, tkmol)

        return mol


    def parseMolecule(self):
        '''
        turn the input into a toolkit molecule according to user settings

        indigo is supposed to read transparently, so we can do away with
        the format setting, basically. If it's numeric, we ask pubchem,
        if it isn't, we consider it a molecule.
        '''
        rawinput = self.data_string

        try:
            pubchemId = int(rawinput)
        except ValueError:
            pubchemId = None

        if pubchemId is not None:
            try:
                url = common.pubchem_url % pubchemId
                pubchemContent = urllib.request.urlopen(url).read()
            except IOError:
                raise common.MCFError('No connection to PubChem')

            self.data_string = pubchemContent

        #common.debug('rpc: %s' % self.rpc)
        #common.debug('data ---\n%s\n---' % self.data_string)

        try:
            tkmol = Indigo().loadMolecule(self.data_string)
        except IndigoException:
            raise common.MCFError("Invalid input data")

        hydrogens = self.options['hydrogens']

        if hydrogens == 'add':
            tkmol.unfoldHydrogens()
            tkmol.layout()  # needed to give coordinates to added Hs

        elif hydrogens == 'delete':
            tkmol.foldHydrogens()

        if not tkmol.hasCoord() or self.options['recalculate_coordinates']:
            tkmol.layout()

        return tkmol


def process(rawargs=None,
            data=None,
            formfields=None,
            progname="mol2chemfig",
            webform=False,
            rpc=False):
    '''
    process is a convenience wrapper for external callers
    '''
    p = Processor(rawargs, data, formfields, progname, webform, rpc)

    try:
        mol = p.process()

    except HelpError as msg:
        return False, msg

    except common.MCFError as msg:    # anticipated error - brief message enough
        msg = traceback.format_exc().splitlines()[-1]
        msg = msg[len('MCFError: '):]
        return False, msg

    except Exception as msg:               # unexpected error - get full traceback
        tb = traceback.format_exc()
        return False, tb

    return True, mol