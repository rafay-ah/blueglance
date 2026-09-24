install_data(
  app_id + '.desktop',
  install_dir: datadir / 'applications',
)

install_data(
  app_id + '.metainfo.xml',
  install_dir: datadir / 'metainfo',
)

install_data(
  'icons' / 'hicolor' / 'scalable' / 'apps' / app_id + '.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
install_data(
  'icons' / 'hicolor' / 'symbolic' / 'apps' / app_id + '-symbolic.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'symbolic' / 'apps',
)

# The UI's own symbolic icons, also in the system theme so tray menus
# (which look icons up by name) can show them.
install_subdir(
  '..' / 'src' / 'blueglance' / 'icons' / 'hicolor' / 'scalable' / 'actions',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable',
)

install_data(
  'blueglance-enable-bluez-battery',
  install_dir: pkglibexecdir,
  install_mode: 'rwxr-xr-x',
)

paths = configuration_data()
paths.set('BINDIR', bindir)
paths.set('PKGLIBEXECDIR', pkglibexecdir)

configure_file(
  input: app_id + '.policy.in',
  output: app_id + '.policy',
  configuration: paths,
  install: true,
  install_dir: datadir / 'polkit-1' / 'actions',
)

if get_option('system_autostart')
  configure_file(
    input: 'autostart.desktop.in',
    output: app_id + '.desktop',
    configuration: paths,
    install: true,
    install_dir: sysconfdir / 'xdg' / 'autostart',
  )
endif

desktop_file_validate = find_program('desktop-file-validate', required: false)
if desktop_file_validate.found()
  test('validate-desktop-file', desktop_file_validate, args: [files(app_id + '.desktop')])
endif

appstreamcli = find_program('appstreamcli', required: false)
if appstreamcli.found()
  test('validate-metainfo', appstreamcli,
    args: ['validate', '--no-net', '--explain', files(app_id + '.metainfo.xml')])
endif
