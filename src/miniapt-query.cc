#include <apt-pkg/init.h>
#include <apt-pkg/pkgsystem.h>

#include <apt-pkg/cachefile.h>
#include <apt-pkg/pkgcache.h>
#include <apt-pkg/policy.h>
#include <apt-pkg/cachefile.h>
#include <apt-pkg/cacheset.h>

#include "miniapt-query.h"

static pkgPolicy *policy;
static pkgCache *cache;
static bool initialized = false;

bool apt_init_system()
{
    initialized = pkgInitConfig(*_config) && pkgInitSystem(*_config, _system);
    return initialized;
}

int check_available(const char *name)
{
    if (!initialized)
        return -1;
    pkgCacheFile cachefile;
    pkgDepCache *depCache = cachefile.GetDepCache();
    if (!depCache)
        return -1;
    cache = cachefile.GetPkgCache();
    if (!cache)
        return -2;
    policy = cachefile.GetPolicy();
    if (!policy)
        return -3;
    if (cachefile->BrokenCount() > 0)
        return -4;

    APT::CacheSetHelper helper(true, GlobalError::NOTICE);
    const char *list[2] = {name, NULL};
    APT::PackageList pkgset = APT::PackageList::FromCommandLine(cachefile, list, helper);
    for (APT::PackageList::const_iterator Pkg = pkgset.begin(); Pkg != pkgset.end(); ++Pkg)
    {
        if (depCache->GetCandidateVersion(Pkg)) {
            if (depCache->MarkInstall(Pkg, true, 0, true)) {
                return 1;
            }
        }
    }
    return 0;
}
