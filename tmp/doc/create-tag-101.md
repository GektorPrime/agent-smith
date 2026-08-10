To create a release tag after accumulating commits on your main branch, here's the typical workflow:
### 1. Check your accumulated commits
#### View recent commits on main
    git log --oneline -20

#### Or count commits since the last tag
    git log $(git describe --tags --abbrev=0)..HEAD --oneline

### 2. Create the tag

> **Agent Smith tags carry NO `v` prefix.** The release installer matches
> `\d+\.\d+\.\d+` exactly (`VERSION_PATTERN` in `agent_smith/scripts/install.py`),
> so `v1.0.0` is rejected as an invalid release version. Tag as `1.0.0`.
> The tag MUST also match `version` in `pyproject.toml`, since `agent-smith-sync`
> derives the package source `git+<GIT_URL>@<version>` from the installed
> package metadata — a mismatch points hosts at a nonexistent ref.

There are two types of tags:
#### Lightweight tag (just a pointer):
    git tag 1.0.0
#### Annotated tag (recommended for releases — includes metadata):
git tag -a 1.0.0 -m "Release 1.0.0: description of changes"
Annotated tags store the tagger name, date, and message, and are generally preferred for releases.

### 3. Push the tag to remote
#### Tags are not pushed automatically with git push. You must push them explicitly:
#### Push a specific tag:
    git push origin 1.0.0
#### Or push all tags at once
    git push origin --tags

### 4. (Optional) Create a GitHub Release
#### If you're using GitHub, you can create a formal release from the tag:
    gh release create 1.0.0 --title "1.0.0" --notes "Release notes here"
#### Or generate notes automatically from commits:
    gh release create 1.0.0 --generate-notes

### Common versioning convention
#### Most projects follow Semantic Versioning (MAJOR.MINOR.PATCH):

| Bump |When | Example |
|---|---|---|
| MAJOR	| Breaking/incompatible changes | 1.0.0 -> 2.0.0 |
| MINOR	| New features, backward-compatible	| 1.0.0 -> 1.1.0 |
| PATCH	| Bug fixes, backward-compatible | 1.0.0 -> 1.0.1 |


### Tips
- Always tag from an up-to-date integration branch: git checkout mainframe && git pull first.
- Use git tag -l to list existing tags before creating a new one.
- Use git describe --tags to see where you are relative to the last tag.
- If you made a mistake, delete a tag with git tag -d 1.0.0 (local) and git push origin --delete 1.0.0 (remote).