project(
  'blueglance',
  version: '0.1.0',
  meson_version: '>= 0.62.0',
  license: 'GPL-3.0-or-later',
)

app_id = 'io.github.rafay_ah.BlueGlance'
extension_uuid = 'blueglance@rafay-ah.github.io'

python = import('python')
gnome = import('gnome')

# Distro packages should pin the system interpreter (-Dpython=/usr/bin/python3):
# PyGObject lives there, not in pyenv/conda/Homebrew interpreters found in PATH.
py = python.find_installation(get_option('python') != '' ? get_option('python') : 'python3')

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')
libexecdir = prefix / get_option('libexecdir')
sysconfdir = get_option('sysconfdir')
pkgdatadir = datadir / meson.project_name()
pkglibexecdir = libexecdir / meson.project_name()

subdir('src')
subdir('data')

if get_option('gnome_extension')
  install_subdir(
    'gnome-extension' / extension_uuid,
    install_dir: datadir / 'gnome-shell' / 'extensions',
  )
endif

gnome.post_install(
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
