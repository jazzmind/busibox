# Shell Troubleshooting Guide

## When Shell Commands Fail with Cryptic Errors

If you encounter errors like these after running a shell command:

```
--: eval: line 1: unexpected EOF while looking for matching `)'
--: eval: line 2: syntax error: unexpected end of file
--: dump_bash_state: command not found
```

Or:
```
permission denied while trying to connect to the docker API
--: line 200: cannot create temp file for here document: Operation not permitted
```

**This indicates the shell state has become corrupted.** The shell maintains state between calls, and certain error conditions (especially sandbox permission errors or interrupted commands) can leave it in a broken state.

## Solutions (in order of preference)

### 1. Use File-Based Tools Instead of Shell
For most tasks, prefer specialized tools over shell commands:
- **Reading files**: Use `Read` tool, not `cat`/`head`/`tail`
- **Searching code**: Use `Grep` tool, not `grep`/`rg` in shell
- **Finding files**: Use `Glob` tool, not `find` in shell
- **Editing files**: Use `StrReplace` tool, not `sed`/`awk` in shell
- **Listing directories**: Use `LS` tool, not `ls` in shell

### 2. Request Proper Permissions
If you must use shell commands that require elevated access:
- For network operations: `required_permissions: ["network"]`
- For git write operations: `required_permissions: ["git_write"]`
- For full access (docker, etc.): `required_permissions: ["all"]`

### 3. Avoid Commands That Can Corrupt Shell State
These patterns are known to cause issues:
- Commands with unbalanced quotes or parentheses in output
- Docker commands without `["all"]` permissions
- Commands that produce very large output
- Commands using `$(...)` or backticks that might be parsed incorrectly

### 4. When Shell is Already Corrupted
The shell state persists across calls. If it's corrupted:

1. **First attempt**: Try a simple command with `["all"]` permissions:
   ```
   echo "test"
   ```

2. **If that fails**: Inform the user that the shell is stuck and ask them to:
   - Open a new terminal in Cursor
   - Or restart the Cursor agent session

3. **Work around it**: Continue working using file-based tools (Read, Write, StrReplace, Grep, Glob) which don't depend on shell state.

## Prevention Tips

1. **Always specify `required_permissions`** when a command might need elevated access
2. **Avoid long-running commands** that might timeout and leave state inconsistent
3. **Use simple command forms** - avoid complex piping when possible
4. **Don't use `$(...)` in shell commands** - it can be parsed incorrectly by the agent framework
5. **Avoid `docker` commands without `["all"]`** - they almost always fail

## Example: Docker Commands

❌ Bad (will likely fail and corrupt shell):
```python
Shell(command="docker ps")
```

✅ Good (proper permissions):
```python
Shell(command="docker ps", required_permissions=["all"])
```

## Example: Complex Formatting

❌ Bad (parentheses in format string can cause parse errors):
```python
Shell(command='docker ps --format "table {{.Names}}\t{{.Ports}}"')
```

✅ Good (simpler form):
```python
Shell(command='docker ps', required_permissions=["all"])
```

## Recovery Message Template

When the shell is corrupted, inform the user:

> "The shell environment has become corrupted after a command error. I can continue working using file-based tools (reading, searching, editing files), but any shell commands will fail until the terminal is reset. You can either:
> 1. Open a new terminal in Cursor and I'll try commands there
> 2. Continue with file-based operations only
> 3. Restart the Cursor agent session"
