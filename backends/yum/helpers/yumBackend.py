# Licensed under the GNU General Public License Version 2
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

# Copyright (C) 2007 
#    Tim Lauridsen <timlau@fedoraproject.org>
#    Seth Vidal <skvidal@fedoraproject.org>
#    Luke Macken <lmacken@redhat.com>
#    James Bowes <jbowes@dangerouslyinc.com>


# imports

import re

from packagekit.backend import *
import yum
from urlgrabber.progress import BaseMeter,format_time,format_number
from yum.rpmtrans import RPMBaseCallback
from yum.constants import *
from yum.update_md import UpdateMetadata
from yum.callbacks import *
from yum.misc import prco_tuple_to_string, unique
import rpmUtils
import exceptions
import types
import signal
import time
from packagekit.backend import PackagekitProgress

# Global vars
yumbase = None
progress = PackagekitProgress()  # Progress object to store the progress

class GPGKeyNotImported(exceptions.Exception):
    pass

def sigquit(signum, frame):   
    print >> sys.stderr, "Quit signal sent - exiting immediately"
    if yumbase:
        print >> sys.stderr, "unlocking Yum"
        yumbase.closeRpmDB()
        yumbase.doUnlock(YUM_PID_FILE)
    sys.exit(1)

class PackageKitYumBackend(PackageKitBaseBackend):

    # Packages there require a reboot
    rebootpkgs = ("kernel", "kernel-smp", "kernel-xen-hypervisor", "kernel-PAE",
              "kernel-xen0", "kernel-xenU", "kernel-xen", "kernel-xen-guest",
              "glibc", "hal", "dbus", "xen")


    def __init__(self,args,lock=True):
        signal.signal(signal.SIGQUIT, sigquit)        
        PackageKitBaseBackend.__init__(self,args)
        self.yumbase = PackageKitYumBase()
        yumbase = self.yumbase
        self._setup_yum()
        if lock:
            self.doLock()
        
 
    def doLock(self):
        ''' Lock Yum'''
        retries = 0
        while not self.isLocked():
            try: # Try to lock yum
                self.yumbase.doLock( YUM_PID_FILE )
                PackageKitBaseBackend.doLock(self)
            except:
                if retries == 0:
                    self.status(STATE_WAIT)
                time.sleep(2)
                retries += 1
                if retries > 100:
                    self.error(ERROR_INTERNAL_ERROR,'Yum is locked by another application')

        
    def unLock(self):        
        ''' Unlock Yum'''
        if self.isLocked():
            PackageKitBaseBackend.unLock(self)
            self.yumbase.closeRpmDB()
            self.yumbase.doUnlock(YUM_PID_FILE)
        
                

    def _get_package_ver(self,po):
        ''' return the a ver as epoch:version-release or version-release, if epoch=0'''
        if po.epoch != '0':
            ver = "%s:%s-%s" % (po.epoch,po.version,po.release)
        else:
            ver = "%s-%s" % (po.version,po.release)
        return ver

    def _do_search(self,searchlist,filters,key):
        '''
        Search for yum packages
        @param searchlist: The yum package fields to search in
        @param filters: package types to search (all,installed,available)
        @param key: key to seach for
        '''
        self.yumbase.doConfigSetup(errorlevel=0,debuglevel=0)# Setup Yum Config
        self.yumbase.conf.cache = 1 # Only look in cache.
        try:
            res = self.yumbase.searchGenerator(searchlist, [key])
            fltlist = filters.split(';')
    
            available = []
            count = 1
            for (pkg,values) in res:
                if count > 100:
                    break
                count+=1
                # are we installed?
                if pkg.repoid == 'installed':
                    if FILTER_NON_INSTALLED not in fltlist:
                        if self._do_extra_filtering(pkg,fltlist):
                            self._show_package(pkg, INFO_INSTALLED)
                else:
                    available.append(pkg)
        except yum.Errors.RepoError,e:
            self.error(ERROR_NO_CACHE,"Yum cache is invalid")

        # Now show available packages.
        if FILTER_INSTALLED not in fltlist:
            for pkg in available:
                if self._do_extra_filtering(pkg,fltlist):
                    self._show_package(pkg, INFO_AVAILABLE)

    def _do_extra_filtering(self,pkg,filterList):
        ''' do extra filtering (gui,devel etc) '''
        for filter in filterList:
            if filter in (FILTER_INSTALLED, FILTER_NON_INSTALLED):
                continue
            elif filter in (FILTER_GUI, FILTER_NON_GUI):
                if not self._do_gui_filtering(filter, pkg):
                    return False
            elif filter in (FILTER_DEVEL, FILTER_NON_DEVEL):
                if not self._do_devel_filtering(filter, pkg):
                    return False
        return True

    def _do_gui_filtering(self,flt,pkg):
        isGUI = False
        if flt == FILTER_GUI:
            wantGUI = True
        else:
            wantGUI = False
        isGUI = wantGUI # Fake it for now
        return isGUI == wantGUI

    def _do_devel_filtering(self,flt,pkg):
        isDevel = False
        if flt == FILTER_DEVEL:
            wantDevel = True
        else:
            wantDevel = False
        regex =  re.compile(r'(-devel)|(-dgb)|(-static)')
        if regex.search(pkg.name):
            isDevel = True
        return isDevel == wantDevel


    def search_name(self,filters,key):
        '''
        Implement the {backend}-search-name functionality
        '''
        self.allow_interrupt(True)
        self.percentage(None)

        searchlist = ['name']
        self._do_search(searchlist, filters, key)

    def search_details(self,filters,key):
        '''
        Implement the {backend}-search-details functionality
        '''
        self.allow_interrupt(True)
        self.percentage(None)

        searchlist = ['name', 'summary', 'description', 'group']
        self._do_search(searchlist, filters, key)

    def search_group(self,filters,key):
        '''
        Implement the {backend}-search-group functionality
        '''
        self.allow_interrupt(True)
        self.percentage(None)
        
        self.error(ERROR_NOT_SUPPORTED,"This function is not implemented in this backend")

    def search_file(self,filters,key):
        '''
        Implement the {backend}-search-file functionality
        '''
        self.allow_interrupt(True)
        self.percentage(None)
        
        #self.yumbase.conf.cache = 1 # Only look in cache.
        fltlist = filters.split(';')
        found = {}
        if not FILTER_NON_INSTALLED in fltlist:
            # Check installed for file
            for pkg in self.yumbase.rpmdb:
                filelist = pkg.filelist
                for fn in filelist:
                    if key in fn and not found.has_key(str(pkg)):
                        if self._do_extra_filtering(pkg, fltlist):
                            self._show_package(pkg, INFO_INSTALLED)
                            found[str(pkg)] = 1
        if not FILTER_INSTALLED in fltlist:
            # Check available for file
            self.yumbase.repos.populateSack(mdtype='filelists')
            for pkg in self.yumbase.pkgSack:
                filelist = pkg.filelist
                for fn in filelist:
                    if key in fn and not found.has_key(str(pkg)):
                        if self._do_extra_filtering(pkg, fltlist):
                            self._show_package(pkg, INFO_AVAILABLE)
                            found[str(pkg)] = 1


    def _getEVR(self,idver):
        '''
        get the e,v,r from the package id version
        '''
        cpos = idver.find(':')
        if cpos != -1:
            epoch = idver[:cpos]
            idver = idver[cpos+1:]
        else:
            epoch = '0'
        (version,release) = tuple(idver.split('-'))
        return epoch,version,release

    def _findPackage(self,id):
        '''
        find a package based on a packahe id (name;version;arch;repoid)
        '''
        # Split up the id
        (n,idver,a,d) = self.get_package_from_id(id)
        # get e,v,r from package id version
        e,v,r = self._getEVR(idver)
        # search the rpmdb for the nevra
        pkgs = self.yumbase.rpmdb.searchNevra(name=n,epoch=e,ver=v,rel=r,arch=a)
        # if the package is found, then return it
        if len(pkgs) != 0:
            return pkgs[0],True
        # search the pkgSack for the nevra
        pkgs = self.yumbase.pkgSack.searchNevra(name=n,epoch=e,ver=v,rel=r,arch=a)
        # if the package is found, then return it
        if len(pkgs) != 0:
            return pkgs[0],False
        else:
            return None,False


    def get_requires(self,package):
        '''
        Print a list of requires for a given package
        '''
        self.allow_interrupt(True)
        self.percentage(None)

        self.error(ERROR_NOT_SUPPORTED,"This function is not implemented in this backend")

    def _is_inst(self,pkg):
        return self.yumbase.rpmdb.installed(po=pkg)

    def _installable(self, pkg, ematch=False):

        """check if the package is reasonably installable, true/false"""
        
        exactarchlist = self.yumbase.conf.exactarchlist        
        # we look through each returned possibility and rule out the
        # ones that we obviously can't use
        
        if self.yumbase.rpmdb.installed(po=pkg):
            return False
        
        # everything installed that matches the name
        installedByKey = self.yumbase.rpmdb.searchNevra(name=pkg.name)
        comparable = []
        for instpo in installedByKey:
            if rpmUtils.arch.isMultiLibArch(instpo.arch) == rpmUtils.arch.isMultiLibArch(pkg.arch):
                comparable.append(instpo)
            else:
                continue
                
        # go through each package 
        if len(comparable) > 0:
            for instpo in comparable:
                if pkg.EVR > instpo.EVR: # we're newer - this is an update, pass to them
                    if instpo.name in exactarchlist:
                        if pkg.arch == instpo.arch:
                            return True
                    else:
                        return True
                        
                elif pkg.EVR == instpo.EVR: # same, ignore
                    return False
                    
                elif pkg.EVR < instpo.EVR: # lesser, check if the pkgtup is an exactmatch
                                   # if so then add it to be installed
                                   # if it can be multiply installed
                                   # this is where we could handle setting 
                                   # it to be an 'oldpackage' revert.
                                   
                    if ematch and self.yumbase.allowedMultipleInstalls(pkg):
                        return True
                        
        else: # we've not got any installed that match n or n+a
            return True
        
        return False
    
        
    def _get_best_dependencies(self,po):
        ''' find the most recent packages that provides the dependencies for a package
        @param po: yum package object to find deps for
        @return: a list for yum package object providing the dependencies 
        '''
        results = self.yumbase.findDeps([po])    
        pkg = results.keys()[0]
        bestdeps=[]
        if len(results[pkg].keys()) == 0: # No dependencies for this package ?
            return bestdeps
        for req in results[pkg].keys():
            reqlist = results[pkg][req] 
            if not reqlist: #  Unsatisfied dependency
                self.error(ERROR_DEP_RESOLUTION_FAILED,"the (%s) requirement could not be resolved" % prco_tuple_to_string(req),exit=False)
                continue
            best = None
            for po in reqlist:
                if best:
                    if po.EVR > best.EVR:
                        best=po
                else:
                    best= po
            bestdeps.append(best)
        return unique(bestdeps)

    def get_depends(self,package):
        '''
        Print a list of depends for a given package
        '''
        self.allow_interrupt(True)
        self.percentage(None)

        name = package.split(';')[0]
        pkg,inst = self._findPackage(package)
        results = {}
        if pkg:
            deps = self._get_best_dependencies(pkg)
        else:
            self.error(ERROR_INTERNAL_ERROR,'Package was not found')
        for pkg in deps:
            if pkg.name != name:
                pkgver = self._get_package_ver(pkg)            
                id = self.get_package_id(pkg.name, pkgver, pkg.arch, pkg.repoid)
                
                if self._is_inst(pkg):
                    self.package(id, INFO_INSTALLED, pkg.summary)
                else:
                    if self._installable(pkg):
                        self.package(id, INFO_AVAILABLE, pkg.summary)


    def update_system(self):
        '''
        Implement the {backend}-update-system functionality
        '''
        self.allow_interrupt(False)
        self.percentage(0)

        txmbr = self.yumbase.update() # Add all updates to Transaction
        if txmbr:
            self._runYumTransaction()
        else:
            self.error(ERROR_INTERNAL_ERROR,"Nothing to do")

    def refresh_cache(self):
        '''
        Implement the {backend}-refresh_cache functionality
        '''
        self.allow_interrupt(True);
        self.percentage(0)

        pct = 0
        try:
            if len(self.yumbase.repos.listEnabled()) == 0:
                self.percentage(100)
                return

            #work out the slice for each one
            bump = (100/len(self.yumbase.repos.listEnabled()))/2

            for repo in self.yumbase.repos.listEnabled():
                repo.metadata_expire = 0
                self.yumbase.repos.populateSack(which=[repo.id], mdtype='metadata', cacheonly=1)
                pct+=bump
                self.percentage(pct)
                self.yumbase.repos.populateSack(which=[repo.id], mdtype='filelists', cacheonly=1)
                pct+=bump
                self.percentage(pct)

            #we might have a rounding error
            self.percentage(100)

        except yum.Errors.YumBaseError, e:
            self.error(ERROR_INTERNAL_ERROR,str(e))

    def resolve(self, filters, name):
        '''
        Implement the {backend}-resolve functionality
        '''
        self.allow_interrupt(True);
        self.percentage(None)

        fltlist = filters.split(';')

        # Get installed packages
        installedByKey = self.yumbase.rpmdb.searchNevra(name=name)
        if FILTER_NON_INSTALLED not in fltlist:
            for pkg in installedByKey:
                self._show_package(pkg,INFO_INSTALLED)
        # Get available packages
        if FILTER_INSTALLED not in fltlist:
            for pkg in self.yumbase.pkgSack.returnNewestByNameArch():
                if pkg.name == name:
                    show = True
                    for instpo in installedByKey:
                        # Check if package have a smaller & equal EVR to a inst pkg
                        if pkg.EVR < instpo.EVR or pkg.EVR == instpo.EVR:
                            show = False
                    if show:
                        self._show_package(pkg,INFO_AVAILABLE)
                        break

    def install(self, package):
        '''
        Implement the {backend}-install functionality
        This will only work with yum 3.2.4 or higher
        '''
        self.allow_interrupt(False)
        self.percentage(0)
    
        pkg,inst = self._findPackage(package)
        if pkg:
            if inst:
                self.error(ERROR_PACKAGE_ALREADY_INSTALLED,'Package already installed')
            try:
                txmbr = self.yumbase.install(name=pkg.name)
                self._runYumTransaction()
            except yum.Errors.InstallError,e:
                print e
                msgs = ';'.join(e)
                self.error(ERROR_PACKAGE_ALREADY_INSTALLED,msgs)
        else:
            self.error(ERROR_PACKAGE_ALREADY_INSTALLED,"Package was not found")

    def _localInstall(self, inst_file):
        """handles installs/updates of rpms provided on the filesystem in a 
           local dir (ie: not from a repo)"""
           
        # Slightly modified localInstall from yum's cli.py
           
        # read in each package into a YumLocalPackage Object
        # append it to self.yumbase.localPackages
        # check if it can be installed or updated based on nevra versus rpmdb
        # don't import the repos until we absolutely need them for depsolving
        
        oldcount = len(self.yumbase.tsInfo)
               
        installpkgs = []
        updatepkgs = []
        
        pkg = inst_file
        try:
            po = yum.packages.YumLocalPackage(ts=self.yumbase.rpmdb.readOnlyTS(), filename=pkg)
        except yum.Errors.MiscError:
            self.error(ERROR_INTERNAL_ERROR,'Cannot open file: %s. Skipping.' % pkg)

        # everything installed that matches the name
        installedByKey = self.yumbase.rpmdb.searchNevra(name=po.name)
        # go through each package 
        if len(installedByKey) == 0: # nothing installed by that name
            installpkgs.append(po)
        else:
            for installed_pkg in installedByKey:
                if po.EVR > installed_pkg.EVR: # we're newer - this is an update, pass to them
                    if installed_pkg.name in self.yumbase.conf.exactarchlist:
                        if po.arch == installed_pkg.arch:
                            updatepkgs.append((po, installed_pkg))
                            continue
                        else:
                            continue
                    else:
                        updatepkgs.append((po, installed_pkg))
                        continue
                elif po.EVR == installed_pkg.EVR:
                    if po.arch != installed_pkg.arch and (isMultiLibArch(po.arch) or
                              isMultiLibArch(installed_pkg.arch)):
                        installpkgs.append(po)
                        continue
                    else:
                        continue
                else:
                    continue
        
        # handle excludes for a localinstall
        toexc = []
        if len(self.yumbase.conf.exclude) > 0:
           exactmatch, matched, unmatched = \
                   yum.packages.parsePackages(installpkgs + map(lambda x: x[0], updatepkgs),
                                 self.yumbase.conf.exclude, casematch=1)
           toexc = exactmatch + matched
       
        # Process potential installs   
        for po in installpkgs:
            if po in toexc:
               continue     # Exclude package
            # Add package to transaction for installation
            self.yumbase.localPackages.append(po)
            self.yumbase.install(po=po)
        # Process potential updates
        for (po, oldpo) in updatepkgs:
            if po in toexc:
               continue # Excludeing package
            # Add Package to transaction for updating
            self.yumbase.localPackages.append(po)
            self.yumbase.tsInfo.addUpdate(po, oldpo)
        


            
    def install_file (self, inst_file):
        '''
        Implement the {backend}-install_file functionality
        Install the package containing the inst_file file
        Needed to be implemented in a sub class
        '''
        self.allow_interrupt(False);
        self.percentage(0)

        pkgs_to_inst = []
        self.yumbase.conf.gpgcheck=0
        self._localInstall(inst_file)
        try:
            # Added the package to the transaction set
            if len(self.yumbase.tsInfo) > 0:
                self._runYumTransaction()
        except yum.Errors.InstallError,e:
            print e
            msgs = ';'.join(e)
            self.error(ERROR_PACKAGE_ALREADY_INSTALLED,msgs)

    def update(self, package):
        '''
        Implement the {backend}-install functionality
        This will only work with yum 3.2.4 or higher
        '''
        self.allow_interrupt(False);
        self.percentage(0)

        pkg,inst = self._findPackage(package)
        if pkg:
            txmbr = self.yumbase.update(name=pkg.name)
            if txmbr:
                self._runYumTransaction()
            else:
                self.error(ERROR_PACKAGE_ALREADY_INSTALLED,"No available updates")
        else:
            self.error(ERROR_PACKAGE_ALREADY_INSTALLED,"No available updates")

    def _check_for_reboot(self):
        for txmbr in self.yumbase.tsInfo:
            pkg = txmbr.po
            # check if package is in reboot list and is installed/updated etc
            print pkg.name,txmbr.output_state
            if pkg.name in self.rebootpkgs and txmbr.ts_state in TS_INSTALL_STATES:
                self.require_restart(RESTART_SYSTEM,"")
                break

    def _runYumTransaction(self,removedeps=None):
        '''
        Run the yum Transaction
        This will only work with yum 3.2.4 or higher
        '''
        rc,msgs =  self.yumbase.buildTransaction()
        if rc !=2:
            retmsg = "Error in Dependency Resolution;" +";".join(msgs)
            self.error(ERROR_DEP_RESOLUTION_FAILED,retmsg)
        else:
            self._check_for_reboot()
            if removedeps == False:
                if len(self.yumbase.tsInfo) > 1:
                    retmsg = 'package could not be remove, because something depends on it'
                    self.error(ERROR_DEP_RESOLUTION_FAILED,retmsg)
                    return

            try:
                rpmDisplay = PackageKitCallback(self)
                callback = ProcessTransPackageKitCallback(self)
                self.yumbase.processTransaction(callback=callback,
                                      rpmDisplay=rpmDisplay)
            except yum.Errors.YumDownloadError, msgs:
                retmsg = "Error in Download;" +";".join(msgs)
                self.error(ERROR_PACKAGE_DOWNLOAD_FAILED,retmsg)
            except yum.Errors.YumGPGCheckError, msgs:
                retmsg = "Error in Package Signatures;" +";".join(msgs)
                self.error(ERROR_INTERNAL_ERROR,retmsg)
            except GPGKeyNotImported, e:
                keyData = self.yumbase.missingGPGKey                
                if not keyData:
                    self.error(ERROR_INTERNAL_ERROR,
                               "GPG key not imported, but no GPG information received from Yum.")

# We need a yum with this change:
# http://devel.linux.duke.edu/gitweb/?p=yum.git;a=commit;h=09640c743fb6a7ade5711183dc7d5964e1bd3221
# to have fingerprint and timestamp available here
# the above change is now in the latest yum for Fedor arawhide (yum-3.2.6-5.fc8)
                self.repo_signature_required(keyData['po'].repoid,
                                             keyData['keyurl'],
                                             keyData['userid'],
                                             keyData['hexkeyid'],
                                             keyData['fingerprint'],
                                             keyData['timestamp'],
                                             'GPG')
                self.error(ERROR_SIGNATURE_NOT_IMPORTED,"GPG key not imported.")
            except yum.Errors.YumBaseError, msgs:
                retmsg = "Error in Transaction Processing;" +";".join(msgs)
                self.error(ERROR_TRANSACTION_ERROR,retmsg)

    def remove(self, allowdep, package):
        '''
        Implement the {backend}-remove functionality
        Needed to be implemented in a sub class
        '''
        self.allow_interrupt(False);
        self.percentage(0)

        pkg,inst = self._findPackage( package)
        if pkg and inst:
            txmbr = self.yumbase.remove(name=pkg.name)
            if txmbr:
                if allowdep != 'yes':
                    self._runYumTransaction(removedeps=False)
                else:
                    self._runYumTransaction(removedeps=True)
            else:
                self.error(ERROR_PACKAGE_NOT_INSTALLED,"Package is not installed")
        else:
            self.error(ERROR_PACKAGE_NOT_INSTALLED,"Package is not installed")


    def get_description(self, package):
        '''
        Print a detailed description for a given package
        '''
        self.allow_interrupt(True)
        self.percentage(None)

        pkg,inst = self._findPackage(package)
        if pkg:
            pkgver = self._get_package_ver(pkg)
            id = self.get_package_id(pkg.name, pkgver, pkg.arch, pkg.repo)
            desc = pkg.description
            desc = desc.replace('\n\n',';')
            desc = desc.replace('\n',' ')

            files = pkg.returnFileEntries('dir')
            files.extend(pkg.returnFileEntries()) # regular files

            file_list = ";".join(files)

            self.description(id, pkg.license, "unknown", desc, pkg.url,
                             pkg.size, file_list)
        else:
            self.error(ERROR_INTERNAL_ERROR,'Package was not found')

    def _show_package(self,pkg,status):
        '''  Show info about package'''
        pkgver = self._get_package_ver(pkg)
        id = self.get_package_id(pkg.name, pkgver, pkg.arch, pkg.repo)
        self.package(id,status, pkg.summary)

    def _get_status(self,notice):
        ut = notice['type']
        # TODO : Add more types to check
        if ut == 'security':
            return INFO_SECURITY
        else:
            return INFO_NORMAL


    def get_updates(self):
        '''
        Implement the {backend}-get-updates functionality
        '''
        self.allow_interrupt(True)
        self.percentage(None)
        md = UpdateMetadata()
        # Added extra Update Metadata
        for repo in self.yumbase.repos.listEnabled():
            try:
                md.add(repo)
            except:
                pass # No updateinfo.xml.gz in repo

        ygl = self.yumbase.doPackageLists(pkgnarrow='updates')
        for pkg in ygl.updates:
            # Get info about package in updates info
            notice = md.get_notice((pkg.name, pkg.version, pkg.release))
            if notice:
                status = self._get_status(notice)
                self._show_package(pkg,status)
            else:
                self._show_package(pkg,INFO_NORMAL)

    def repo_enable(self, repoid, enable):
        '''
        Implement the {backend}-repo-enable functionality
        '''
        try:
            repo = self.yumbase.repos.getRepo(repoid)
            if enable == 'false':
                if repo.isEnabled():
                    repo.disablePersistent()
            else:
                if not repo.isEnabled():
                    repo.enablePersistent()
                
        except yum.Errors.RepoError,e:
            self.error(ERROR_REPO_NOT_FOUND, "repo %s is not found" % repoid)
        

    def get_repo_list(self):
        '''
        Implement the {backend}-get-repo-list functionality
        '''
        for repo in self.yumbase.repos.repos.values():
            if repo.isEnabled():
                self.repo_detail(repo.id,repo.name,'true')
            else:
                self.repo_detail(repo.id,repo.name,'false')

    def get_update_detail(self,package):
        '''
        Implement the {backend}-get-update_detail functionality
        '''
        self.error(ERROR_NOT_SUPPORTED,"This function is not implemented in this backend")
                

    def _setup_yum(self):
        self.yumbase.doConfigSetup(errorlevel=0,debuglevel=0)     # Setup Yum Config
        self.yumbase.conf.throttle = "40%"                        # Set bandwidth throttle to 40%
        self.dnlCallback = DownloadCallback(self,showNames=True)  # Download callback
        self.yumbase.repos.setProgressBar( self.dnlCallback )     # Setup the download callback class

class DownloadCallback( BaseMeter ):
    """ Customized version of urlgrabber.progress.BaseMeter class """
    def __init__(self,base,showNames = False):
        BaseMeter.__init__( self )
        self.totSize = ""
        self.base = base
        self.showNames = showNames
        self.oldName = None
        self.lastPct = 0
        self.totalPct = 0
        self.pkgs = None
        self.numPkgs=0
        self.bump = 0.0

    def setPackages(self,pkgs,startPct,numPct):
        self.pkgs = pkgs
        self.numPkgs = len(self.pkgs)
        self.bump = numPct/self.numPkgs
        self.totalPct = startPct

    def _getPackage(self,name):
        name = name.split('-')[0]
        if self.pkgs:
            for pkg in self.pkgs:
                if pkg.name == name:
                    return pkg
        return None

    def update( self, amount_read, now=None ):
        BaseMeter.update( self, amount_read, now )

    def _do_start( self, now=None ):
        name = self._getName()
        self.updateProgress(name,0.0,"","")
        if not self.size is None:
            self.totSize = format_number( self.size )

    def _do_update( self, amount_read, now=None ):
        fread = format_number( amount_read )
        name = self._getName()
        if self.size is None:
            # Elapsed time
            etime = self.re.elapsed_time()
            fetime = format_time( etime )
            frac = 0.0
            self.updateProgress(name,frac,fread,fetime)
        else:
            # Remaining time
            rtime = self.re.remaining_time()
            frtime = format_time( rtime )
            frac = self.re.fraction_read()
            self.updateProgress(name,frac,fread,frtime)


    def _do_end( self, amount_read, now=None ):
        total_time = format_time( self.re.elapsed_time() )
        total_size = format_number( amount_read )
        name = self._getName()
        self.updateProgress(name,1.0,total_size,total_time)

    def _getName(self):
        '''
        Get the name of the package being downloaded
        '''
        if self.text and type( self.text ) == type( "" ):
            name = self.text
        else:
            name = self.basename
        return name

    def updateProgress(self,name,frac,fread,ftime):
        '''
         Update the progressbar (Overload in child class)
        @param name: filename
        @param frac: Progress fracment (0 -> 1)
        @param fread: formated string containing BytesRead
        @param ftime : formated string containing remaining or elapsed time
        '''
        pct = int( frac*100 )
        if self.lastPct != pct:
            self.lastPct = pct
            # bump the sub persentage for this package
            self.base.sub_percentage(int( frac*100 ))
        if name != self.oldName:
            self.oldName = name
            if self.bump > 0.0: # Bump the total download percentage
                self.totalPct += self.bump
                self.base.percentage(int(self.totalPct))
            if self.showNames:
                pkg = self._getPackage(name)
                if pkg: # show package to download
                    self.base._show_package(pkg,INFO_DOWNLOADING)


class PackageKitCallback(RPMBaseCallback):
    def __init__(self,base):
        RPMBaseCallback.__init__(self)
        self.base = base
        self.pct = 0
        self.curpkg = None
        self.startPct = 50
        self.numPct = 50

    def _calcTotalPct(self,ts_current,ts_total):
        bump = float(self.numPct)/ts_total
        pct = int(self.startPct + (ts_current * bump))
        return pct

    def _showName(self,status):
        if type(self.curpkg) in types.StringTypes:
            id = self.base.get_package_id(self.curpkg,'','','')
        else:
            pkgver = self.base._get_package_ver(self.curpkg)
            id = self.base.get_package_id(self.curpkg.name, pkgver, self.curpkg.arch, self.curpkg.repo)
        self.base.package(id,status, "")


    def event(self, package, action, te_current, te_total, ts_current, ts_total):
        if str(package) != str(self.curpkg):
            self.curpkg = package
            if action in TS_INSTALL_STATES:
                self.base.status(STATE_INSTALL)
                status = INFO_INSTALLING
            elif action in TS_REMOVE_STATES:
                self.base.status(STATE_REMOVE)
                status = INFO_REMOVING
            self._showName(status)
            pct = self._calcTotalPct(ts_current, ts_total)
            self.base.percentage(pct)
        val = (ts_current*100L)/ts_total
        if val != self.pct:
            self.pct = val
            self.base.sub_percentage(val)

    def errorlog(self, msg):
        # grrrrrrrr
        pass

class ProcessTransPackageKitCallback:
    def __init__(self,base):
        self.base = base

    def event(self,state,data=None):
        if state == PT_DOWNLOAD:        # Start Downloading
            self.base.allow_interrupt(True)
            self.base.percentage(10)
            self.base.status(STATE_DOWNLOAD)
        if state == PT_DOWNLOAD_PKGS:   # Packages to download
            self.base.dnlCallback.setPackages(data,10,30)
        elif state == PT_GPGCHECK:
            self.base.percentage(40)
            pass
        elif state == PT_TEST_TRANS:
            self.base.allow_interrupt(False)
            self.base.percentage(45)
            pass
        elif state == PT_TRANSACTION:
            self.base.allow_interrupt(False)
            self.base.percentage(50)
            pass


class PackageKitYumBase(yum.YumBase):
    """
    Subclass of YumBase.  Needed so we can overload _checkSignatures
    and nab the gpg sig data
    """

    def __init__(self):
        yum.YumBase.__init__(self)
        self.missingGPGKey = None

    def _checkSignatures(self,pkgs,callback):
        ''' The the signatures of the downloaded packages '''
        # This can be overloaded by a subclass.

        for po in pkgs:
            result, errmsg = self.sigCheckPkg(po)
            if result == 0:
                # Verified ok, or verify not req'd
                continue            
            elif result == 1:
                self.getKeyForPackage(po, fullaskcb=self._fullAskForGPGKeyImport)
            else:
                raise yum.Errors.YumGPGCheckError, errmsg

        return 0

    def _fullAskForGPGKeyImport(self, data):
        self.missingGPGKey = data

        raise GPGKeyNotImported()

    def _askForGPGKeyImport(self, po, userid, hexkeyid):
        ''' 
        Ask for GPGKeyImport 
        '''
        # TODO: Add code here to send the RepoSignatureRequired signal
        return False
