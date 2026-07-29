# Python 单元测试套件
#
# 设计原则：
# - 所有被测目标必须是 gi-free（不 import gi），使其可在无 GTK 显示的
#   CI/headless 环境下直接跑 unittest，无需 xvfb-run。
# - 用 unittest 标准库（无外部依赖）；meson 的 test() 原生支持任意可执行命令。
# - 通过 PYTHONPATH=meson.source_root() 让 `import setzer.helpers.*` 可解析。
#
# 开发期跑测试：
#   PYTHONPATH=/home/sam/Desktop/Setzer python -m unittest discover -s tests/python -v
# 或通过 meson：
#   meson test -C builddir
#   meson test -C builddir test_persistence

python_bin = import('python').find_installation()

test_env = environment()
test_env.set('PYTHONPATH', meson.source_root())
test_env.set('LC_ALL', 'C')

foreach t : [
  ['test_persistence',            'test_persistence.py'],
  ['test_settings_migration',     'test_settings_migration.py'],
  ['test_workspace_session',      'test_workspace_session.py'],
  ['test_wizard_presets',         'test_wizard_presets.py'],
  ['test_wizard_page_map',        'test_wizard_page_map.py'],
  ['test_wizard_font_package',    'test_wizard_font_package.py'],
  ['test_symbol_categories',      'test_symbol_categories.py'],
  ['test_stats_markup',           'test_stats_markup.py'],
  ['test_matching_blocks_regex',  'test_matching_blocks_regex.py'],
  ['test_help_search',            'test_help_search.py'],
  ['test_latex_db_error_flag',    'test_latex_db_error_flag.py'],
  ['test_line_ending',            'test_line_ending.py'],
]
  test(
    t[0],
    python_bin,
    args: ['-m', 'unittest', '-v', join_paths(meson.current_source_dir(), t[1])],
    env: test_env,
    workdir: meson.source_root(),
  )
endforeach
