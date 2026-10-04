# Upstream work and licenses

This repository's original scripts, documentation, GNOME extension and trackpad app are provided under the [MIT license](LICENSE). This does not relicense kernel code or separately downloaded drivers.

- [leifliddy/macbook12-audio-driver](https://github.com/leifliddy/macbook12-audio-driver) supplies the CS4208 codec changes, building on [davidjo/snd_hda_macbookpro](https://github.com/davidjo/snd_hda_macbookpro). The build script fetches the pinned commit documented in the audio guide. Downloaded files retain their upstream notices and licenses.
- [leifliddy/macbook12-bluetooth-driver](https://github.com/leifliddy/macbook12-bluetooth-driver) is the source of the Bluetooth power-sequencing workaround. This project applies the documented small change to matching Debian kernel sources.
- [Linux](https://www.kernel.org/) and Debian's kernel source packages retain their GPL and file-specific licenses. Kernel sources and compiled modules are not distributed in this repository.
- [libinput](https://gitlab.freedesktop.org/libinput/libinput) provides the public custom acceleration API and the acceleration model used as the trackpad curve's basis. The app's C and Python implementations are kept in agreement by isolated tests.
- [keyd](https://github.com/rvaiya/keyd), [GNOME Shell](https://gitlab.gnome.org/GNOME/gnome-shell), GTK and libadwaita are installed from Debian and remain separate projects under their respective licenses.
- The keyboard identifier calculation is a small port of keyd's algorithm. Its MIT/X Consortium copyright notice is retained in [keyboard/KEYD-LICENSE](keyboard/KEYD-LICENSE).

Credit also goes to MacBook Linux users who documented the Apple NVMe D3cold resume workaround. The feature guides link to technical references where relevant.
