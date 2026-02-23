# unspoken-ng

Unspoken for modern NVDA, using OpenAL Soft for HRTF spatialization and EFX reverb.

## Why?

Unfortunately, previous versions of Unspoken had many serious problems due to the fact they depended on an unmaintained audio library:
* the output device of the sounds could not be changed
* after running for several hours, the audio device would begin to make a squealing sound
* When NVDA upgrades to 64-bit, or beyond Python 3.11, the library can no longer be used

## The Solution

This version of Unspoken uses OpenAL Soft for HRTF spatialization and EFX reverb via a
pure-Python ctypes wrapper. OpenAL Soft is an actively maintained, cross-platform OpenAL
implementation with a permissive MIT license.

## Credits

In the case of this project, I'm really just the releaser, documenter, and contact guy.  Unspoken-ng wouldn't be possible without:
* Bryan Smart: the original work on two versions of the Unspoken addon
* Masonasons: updating the Unspoken addon with the API changes in 2023 and 2024
* Ambro86: maintaining modern Python bindings for synthizer, as well as contributing some code to unspoken
* Tyler Spivey: for sitting down, figuring out steam audio, and creating Python bindings that do what we need
* Me: for really needing this functionality, doing what I could to keep it going, and bothering other people to help with all the hard bits

## Using the addon

The addon, once installed, will create a new category under settings called "unspoken".  Here, you can turn the sounds on and off, change if NVDA will announce control types as well as play the sounds, and configure reverb settings.

## Building

Build the NVDA addon using scons. No external C compiler or SDK is required; the audio
backend is pure Python using ctypes.

Place `soft_oal.dll` (OpenAL Soft official Windows x64 build, ~2MB, MIT license) in
`addon/globalPlugins/Unspoken/` before building. Download from https://openal-soft.org/

## Known Issues

If you would like to fix any of these issues, pull requests will be happily and gratefully accepted:
1. No translation support: it's unclear to me what needs to happen here. I need to make some kind of cloud account for some sort of crowd service or something?
2. Unspoken-ng does not play sounds while arrowing through some controls on the web.  This is because we can't get the position of a control until the focus moves to it, and NVDA no longer moves system focus with the browse cursor.  We should be able to fix this by copying parts of the way earcons does things. I just haven't gotten there yet.

## Maintenance commitment

I, Samuel Proulx AKA fastfinge, publicly commit to maintaining the currently existing functionality of all addon features present in the fastfinge/unspoken-ng repository going forward, in order to keep up with API changes to NVDA.  Should I be unable to do so, I will hire someone else to do so on my behalf.  I depend on this functionality for some critical workflows myself.  However, the addon meets my needs as it stands.  Should you wish to tackle any of the known issues above, you are warmly welcomed and invited to submit a PR.  When I accept it, I will maintain the added functionality.  But these issues do not impact my workflow, so I will not work on the above issues myself.
