"""A session is a terminal.

Three stored fields -- id, cwd, cmd -- plus whatever tmux can be asked about
right now. No windows, no panes: the multiplexing half of tmux is not used
here, because the caller is already doing it (works/02-session.md §1).

Five members, and that is the whole surface: ``send``, ``send_key``,
``rename``, ``kill``, and the ``url`` you can hand to a person.
"""

from .errors import NoSuchSession


class Session:
    __slots__ = ("id", "cwd", "cmd", "created_at", "last_attached", "external", "_t")

    def __init__(self, tmuxd, record):
        self._t = tmuxd
        self.id = record["id"]
        self.cwd = record.get("cwd")
        self.cmd = record.get("cmd")
        self.created_at = record.get("created_at")
        self.last_attached = record.get("last_attached")
        self.external = bool(record.get("external"))

    # -- live state, asked of tmux every time ---------------------------

    @property
    def status(self):
        return "alive" if self._t._tmux.has_session(self.id) else "exited"

    @property
    def alive(self):
        return self.status == "alive"

    @property
    def clients(self):
        return self._t._tmux.count_clients(self.id)

    @property
    def current_command(self):
        return self._t._tmux.display(self.id, "#{pane_current_command}")

    @property
    def url(self):
        """The entrance. ttyd's own ``?arg=`` address, no redirect involved."""
        return self._t.url_for(self.id)

    # -- the only write action ------------------------------------------

    def send(self, text, enter=False):
        """Type ``text`` literally. Not one character is interpreted.

        Separate from :meth:`send_key` on purpose: ``tmux send-keys`` without
        ``-l`` reads its arguments as key *names*, so sending the sentence
        "Enter the code" would press Return. Splitting the two at the API
        surface removes the trap rather than documenting it.

        Returns once the characters are handed to tmux. That is all it means:
        not that the command finished, and not that it succeeded.
        """
        self._require()
        if text:
            self._t._tmux.send_literal(self.id, text)
        if enter:
            self._t._tmux.send_keys(self.id, ["Enter"])
        return self

    def send_key(self, *keys):
        """Press tmux key names: ``C-c``, ``Enter``, ``Escape``, ``Up`` ..."""
        self._require()
        if keys:
            self._t._tmux.send_keys(self.id, [str(k) for k in keys])
        return self

    # -- lifecycle -------------------------------------------------------

    def rename(self, new_id):
        self._t._validate_id(new_id)
        self._require()
        if self._t._tmux.has_session(new_id):
            from .errors import SessionExists

            raise SessionExists('id "%s" already has a session' % new_id, id=new_id)
        self._t._tmux.rename_session(self.id, new_id)
        self._t._store.rename(self.id, new_id)
        self.id = new_id
        return self

    def kill(self):
        """Destroy the session. Nothing else does -- not detach, not exit.

        Killing while people are attached goes ahead (tmux throws them out);
        the client count is returned so a UI can explain what just happened.
        """
        clients = 0
        if self._t._tmux.has_session(self.id):
            clients = self._t._tmux.count_clients(self.id)
            self._t._tmux.kill_session(self.id)
        self._t._store.delete(self.id)
        return clients

    # -- plumbing --------------------------------------------------------

    def _require(self):
        if not self._t._tmux.has_session(self.id):
            raise NoSuchSession('no session with id "%s"' % self.id, id=self.id)

    def to_dict(self):
        d = {
            "id": self.id,
            "cwd": self.cwd,
            "cmd": self.cmd,
            "status": self.status,
            "clients": self.clients,
            "current_command": self.current_command,
            "created_at": self.created_at,
            "last_attached": self.last_attached,
            "url": self.url,
        }
        if self.external:
            d["external"] = True
        return d

    def __repr__(self):
        return "<Session %s cmd=%r>" % (self.id, self.cmd)
