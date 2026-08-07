To create a release tag after accumulating commits on your main branch, here's the typical workflow:
### 1. Check your accumulated commits
#### View recent commits on main
    git log --oneline -20

#### Or count commits since the last tag
    git log $(git describe --tags --abbrev=0)..HEAD --oneline

### 2. Create the tag
There are two types of tags:
#### Lightweight tag (just a pointer):
    git tag v1.0.0
#### Annotated tag (recommended for releases — includes metadata):
git tag -a v1.0.0 -m "Release v1.0.0: description of changes"
Annotated tags store the tagger name, date, and message, and are generally preferred for releases.

### 3. Push the tag to remote
#### Tags are not pushed automatically with git push. You must push them explicitly:
#### Push a specific tag:
    git push origin v1.0.0
#### Or push all tags at once
    git push origin --tags

### 4. (Optional) Create a GitHub Release
#### If you're using GitHub, you can create a formal release from the tag:
    gh release create v1.0.0 --title "v1.0.0" --notes "Release notes here"
#### Or generate notes automatically from commits:
    gh release create v1.0.0 --generate-notes

### Common versioning convention
#### Most projects follow Semantic Versioning (MAJOR.MINOR.PATCH):

| Bump |When | Example |
|---|---|---|
| MAJOR	| Breaking/incompatible changes | v1.0.0 -> v2.0.0 |
| MINOR	| New features, backward-compatible	| v1.0.0 -> v1.1.0 |
| PATCH	| Bug fixes, backward-compatible | v1.0.0 -> v1.0.1 |


### Tips
- Always tag from an up-to-date main branch: git checkout main && git pull first.
- Use git tag -l to list existing tags before creating a new one.
- Use git describe --tags to see where you are relative to the last tag.
- If you made a mistake, delete a tag with git tag -d v1.0.0 (local) and git push origin --delete v1.0.0 (remote).