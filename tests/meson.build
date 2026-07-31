# Validate metainfo / .desktop files — these only exist on non-Windows
# (data/meson.build installs them only when host_machine.system() != 'windows').
if host_machine.system() != 'windows'
  # Validate metainfo file
  appstreamcli = find_program(
    'appstreamcli',
    required: false,
  )
  if appstreamcli.found()
    test(
      'validate metainfo file',
      appstreamcli,
      args: ['validate', '--no-net', '--pedantic', metainfo_file],
    )
  endif

  # Validate .desktop file
  desktop_file_validate =  find_program(
    'desktop-file-validate',
    required: false,
  )
  if desktop_file_validate.found()
    test(
      'validate .desktop file',
      desktop_file_validate,
      args: [desktop_file]
    )
  endif
endif

# Python 单元测试（gi-free 模块，无 GTK 依赖）
subdir('python')
