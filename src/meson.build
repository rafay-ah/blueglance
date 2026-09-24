conf = configuration_data()
conf.set('PYTHON', py.full_path())
conf.set('VERSION', meson.project_version())
conf.set('PKGDATADIR', pkgdatadir)
conf.set('PKGLIBEXECDIR', pkglibexecdir)

configure_file(
  input: 'blueglance.in',
  output: 'blueglance',
  configuration: conf,
  install: true,
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)

install_subdir(
  'blueglance',
  install_dir: pkgdatadir,
  exclude_directories: ['__pycache__', 'devices/__pycache__', 'services/__pycache__', 'ui/__pycache__',
                        'session/__pycache__'],
)
