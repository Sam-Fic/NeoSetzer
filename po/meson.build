i18n = import('i18n')

i18n.gettext(
  'setzer',
  install_dir: localedir,
  args: [
    '--msgid-bugs-address=https://github.com/Sam-Fic/NeoSetzer/issues',
    # 复数包装函数 _ngettext(singular, plural, n)（build_status_text 等处）：
    # xgettext 默认只识别直接的 ngettext 调用，包装名需显式声明参数位。
    '--keyword=_ngettext:1,2',
  ],
)
