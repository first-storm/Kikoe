"""A searchable list of models, pushed from the dialogs that pick one."""

from collections.abc import Callable, Sequence

from gi.repository import Adw, Gio, GObject, Gtk

from kikoe.core.choice import ModelChoice, model_matches
from kikoe.core.models import ModelKey
from kikoe.ui.guard import Guard
from kikoe.ui.items import ModelChoiceItem
from kikoe.ui.labels import capability_summary
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("model_chooser_page"))
class ModelChooserPage(Adw.NavigationPage):
    """Long model lists get their own page so the options stay visible on the first one."""

    __gtype_name__ = "KikoeModelChooserPage"

    header_bar: Adw.HeaderBar = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    stack: Gtk.Stack = Gtk.Template.Child()
    results_page: Adw.PreferencesPage = Gtk.Template.Child()

    def __init__(
        self,
        choices: Sequence[ModelChoice],
        current: ModelKey | None,
        on_chosen: Callable[[ModelChoice], None],
        *,
        closable: bool = True,
    ) -> None:
        """``closable`` False hides Close for dialogs that offer Cancel instead."""
        super().__init__()
        self.header_bar.set_show_end_title_buttons(closable)
        self._current = current
        self._on_chosen = on_chosen
        self._query = ""
        self._filter = Gtk.CustomFilter.new(self._matches)
        self._radio_anchor = Gtk.CheckButton()
        """Never shown; a lone model still needs a group to look like a radio, not a check."""
        self._updating = Guard()
        self._lists: list[tuple[Adw.PreferencesGroup, Gtk.FilterListModel]] = []
        self._add_provider_groups(choices)
        # Typing anywhere on the page searches, as in other GNOME lists.
        self.search_entry.set_key_capture_widget(self)

    def set_current(self, current: ModelKey | None) -> None:
        """Mark ``current`` and clear the previous search before the page is shown again."""
        self._current = current
        self._query = ""
        self.search_entry.set_text("")
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        with self._updating:
            self._radio_anchor.set_active(True)
            if (radio := self._radio_of(current)) is not None:
                radio.set_active(True)

    def _radio_of(self, key: ModelKey | None) -> Gtk.CheckButton | None:
        for group, matches in self._lists:
            for position in range(matches.get_n_items()):
                item = matches.get_item(position)
                if isinstance(item, ModelChoiceItem) and item.choice.key == key:
                    row = group.get_row(position)
                    radio = row.get_activatable_widget() if isinstance(row, Adw.ActionRow) else None
                    return radio if isinstance(radio, Gtk.CheckButton) else None
        return None

    def _add_provider_groups(self, choices: Sequence[ModelChoice]) -> None:
        stores: dict[str, Gio.ListStore[ModelChoiceItem]] = {}
        for choice in choices:
            provider_id = choice.provider.provider_id
            if provider_id not in stores:
                stores[provider_id] = Gio.ListStore(item_type=ModelChoiceItem)
                self._add_group(choice.provider.name, stores[provider_id])
            stores[provider_id].append(ModelChoiceItem(choice))

    def _add_group(self, title: str, store: Gio.ListStore[ModelChoiceItem]) -> None:
        group = Adw.PreferencesGroup(title=title)
        matches = Gtk.FilterListModel(model=store, filter=self._filter)
        matches.connect("notify::n-items", self._on_matches_notify_n_items)
        group.bind_model(matches, self._create_row)
        self.results_page.add(group)
        self._lists.append((group, matches))

    def _matches(self, item: GObject.Object) -> bool:
        return isinstance(item, ModelChoiceItem) and model_matches(item.choice, self._query)

    def _create_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, ModelChoiceItem)
        choice = item.choice
        radio = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=choice.key == self._current)
        radio.set_group(self._radio_anchor)
        radio.connect("toggled", self._on_model_radio_toggled, choice)
        row = Adw.ActionRow(
            title=choice.model.display_name,
            subtitle=capability_summary(choice.model.capabilities),
            use_markup=False,
            activatable_widget=radio,
        )
        row.add_prefix(radio)
        # Activating the row of the model already in use returns without a toggle.
        row.connect("activated", self._on_model_row_activated, choice)
        return row

    def _on_matches_notify_n_items(
        self, matches: Gtk.FilterListModel, _spec: GObject.ParamSpec
    ) -> None:
        for group, model in self._lists:
            if model is matches:
                group.set_visible(model.get_n_items() > 0)
        found = any(model.get_n_items() for _group, model in self._lists)
        self.stack.set_visible_child_name("results" if found else "empty")

    def _first_match(self) -> ModelChoice | None:
        for _group, matches in self._lists:
            item = matches.get_item(0)
            if isinstance(item, ModelChoiceItem):
                return item.choice
        return None

    def _focus_current(self) -> None:
        """Start at the model in use, scrolled into view, so the list never opens lost."""
        radio = self._radio_of(self._current)
        row = radio.get_ancestor(Adw.ActionRow) if radio is not None else None
        if row is None or not row.grab_focus():
            self.search_entry.grab_focus()

    def _on_model_radio_toggled(self, radio: Gtk.CheckButton, choice: ModelChoice) -> None:
        if radio.get_active() and not self._updating.active:
            self._on_chosen(choice)

    def _on_model_row_activated(self, _row: Adw.ActionRow, choice: ModelChoice) -> None:
        self._on_chosen(choice)

    @Gtk.Template.Callback()
    def _on_page_shown(self, _page: Adw.NavigationPage) -> None:
        self._focus_current()

    @Gtk.Template.Callback()
    def _on_search_entry_search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text()
        if query == self._query:
            return
        self._query = query
        self._filter.changed(Gtk.FilterChange.DIFFERENT)

    @Gtk.Template.Callback()
    def _on_search_entry_activate(self, _entry: Gtk.SearchEntry) -> None:
        """Enter picks the first match, so a search can finish without leaving the keyboard."""
        if not self._query.strip():
            return
        if (choice := self._first_match()) is not None:
            self._on_chosen(choice)

    @Gtk.Template.Callback()
    def _on_search_entry_stop_search(self, entry: Gtk.SearchEntry) -> None:
        """Escape clears the search first and goes back once it is empty."""
        if entry.get_text():
            entry.set_text("")
            return
        self.activate_action("navigation.pop", None)
