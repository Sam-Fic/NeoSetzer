#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA


import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.app.service_locator import ServiceLocator


class AboutDialog(object):

    def __init__(self, main_window):
        self.main_window = main_window

    def run(self):
        self.setup()
        self.view.present(self.main_window)

    def setup(self):
        self.view = Adw.AboutDialog()
        self.view.set_application_name('NeoSetzer')
        self.view.set_application_icon('org.cvfosammmm.Setzer')
        self.view.set_version(ServiceLocator.get_setzer_version())
        self.view.set_developer_name('Robert Griesel')
        self.view.set_copyright('© 2017-present Robert Griesel, Sam-Fic')
        self.view.set_comments(_('NeoSetzer is a LaTeX editor.'))
        self.view.set_license_type(Gtk.License.GPL_3_0)
        self.view.set_website('https://www.cvfosammmm.org/setzer/')
        self.view.set_support_url('https://github.com/Sam-Fic/Setzer/discussions')
        self.view.set_issue_url('https://github.com/Sam-Fic/Setzer/issues')
        self.view.add_link(_('Fork Repository'), 'https://github.com/Sam-Fic/Setzer')
        # libadwaita 的 set_release_notes() 要求内容是合法 XML（含根元素）
        # 的 HTML 子集，裸纯文本无 <p>/<ul> 根会被解析器拒绝
        # （"Document must begin with an element"）。用 <ul><li> 包裹成列表。
        self.view.set_release_notes(_('''
<ul>
<li>Performance improvements and bug fixes</li>
<li>Improved keyboard shortcuts</li>
<li>Added print functionality</li>
<li>Enhanced preview keyboard navigation</li>
<li>Better error handling for file operations</li>
</ul>'''))
        import platform
        debug_info = 'Setzer version: {}\nOS: {} {}\nPython: {}'.format(
            ServiceLocator.get_setzer_version(),
            platform.system(), platform.release(),
            platform.python_version())
        self.view.set_debug_info(debug_info)
        self.view.set_developers(('Robert Griesel',))
        # Fork maintainer: shown as a separate credits section with a clickable
        # GitHub link. Adw.AboutDialog auto-detects URLs in credit entries.
        self.view.add_credit_section(_('Fork maintainer'), ['Sam-Fic https://github.com/Sam-Fic'])
        # TRANSLATORS: 'Name <email@domain.com>' or 'Name https://website.example'
        self.view.set_translator_credits(_('translator-credits'))
