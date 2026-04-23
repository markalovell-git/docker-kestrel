# TODO

## network_map port conflict detection

- [ ] `port_conflicts` in `network_map` is effectively dead code for standard bridge networking — Docker prevents duplicate port bindings at the daemon level. Either document this limitation or remove the check. Only relevant for `--network host` containers where Docker doesn't manage port binding.

## After publishing to PyPI

- [ ] Remove the "Not yet published to PyPI" warning from README.md
- [ ] Remove the "Before PyPI publish (local install)" section from README.md
- [ ] Keep only the "After PyPI publish" instructions and drop that heading too (it becomes the default)
- [ ] Update `~/.cursor/mcp.json` to use `["docker-kestrel"]` instead of `["--from", "/path/to/...", "docker-kestrel"]`
