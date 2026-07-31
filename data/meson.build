# GNOME 桌面集成（.desktop / icon / mime / metainfo / man）仅适用于 Linux。
# Windows 无这些概念，跳过安装以避免 meson 报错或产生无用文件。
if host_machine.system() != 'windows'

  # .desktop file
  desktop_file =files('org.cvfosammmm.Setzer.desktop')
  install_data(
    desktop_file,
    install_dir: join_paths(datadir, 'applications'),
  )

  # icon
  install_data(
    files('org.cvfosammmm.Setzer.svg'),
    install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
  )

  # MIME type
  install_data(
    files('org.cvfosammmm.Setzer.mime.xml'),
    install_dir: join_paths(datadir, 'mime', 'packages'),
  )

  # metainfo
  metainfo_file = i18n.merge_file(
    input:  files('org.cvfosammmm.Setzer.metainfo.xml.in'),
    output: 'org.cvfosammmm.Setzer.metainfo.xml',
    type: 'xml',
    po_dir: join_paths(meson.source_root(), 'po'),
    install: true,
    install_dir: join_paths(datadir, 'metainfo'),
  )

  # man page
  config_man = configuration_data()
  config_man.set('VERSION', meson.project_version())

  man_file = configure_file(
    input: files('setzer.1.in'),
    output: 'setzer.1',
    configuration: config_man,
  )

  install_man(
    man_file,
    install_dir: join_paths(mandir, 'man1'),
  )

endif
