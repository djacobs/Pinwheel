# Pinwheel Fates -- Full Cycle Demo

*2026-02-16T16:47:08Z by Showboat 0.5.0*

**Pinwheel Fates** is a simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???. The AI serves as a reporter -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application.

## Step 1: Seed the League

Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule.

```bash
uv run python scripts/demo_seed.py seed
```

```output
Traceback (most recent call last):
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
    self.dialect.do_execute(
    ~~~~~~~~~~~~~~~~~~~~~~~^
        cursor, str_statement, effective_parameters, context
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
    cursor.execute(statement, parameters)
    ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/dialects/sqlite/aiosqlite.py", line 182, in execute
    self._adapt_connection._handle_exception(error)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/dialects/sqlite/aiosqlite.py", line 342, in _handle_exception
    raise error
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/dialects/sqlite/aiosqlite.py", line 164, in execute
    self.await_(_cursor.execute(operation, parameters))
    ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 132, in await_only
    return current.parent.switch(awaitable)  # type: ignore[no-any-return,attr-defined] # noqa: E501
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 196, in greenlet_spawn
    value = await result
            ^^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/aiosqlite/cursor.py", line 40, in execute
    await self._execute(self._cursor.execute, sql, parameters)
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/aiosqlite/cursor.py", line 32, in _execute
    return await self._conn._execute(fn, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/aiosqlite/core.py", line 160, in _execute
    return await future
           ^^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/aiosqlite/core.py", line 63, in _connection_worker_thread
    result = function()
sqlite3.OperationalError: disk I/O error

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/Users/djacobs/Documents/GitHub/Pinwheel/scripts/demo_seed.py", line 659, in <module>
    main()
    ~~~~^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/scripts/demo_seed.py", line 636, in main
    asyncio.run(seed())
    ~~~~~~~~~~~^^^^^^^^
  File "/Users/djacobs/.local/share/uv/python/cpython-3.13.2-macos-aarch64-none/lib/python3.13/asyncio/runners.py", line 195, in run
    return runner.run(main)
           ~~~~~~~~~~^^^^^^
  File "/Users/djacobs/.local/share/uv/python/cpython-3.13.2-macos-aarch64-none/lib/python3.13/asyncio/runners.py", line 118, in run
    return self._loop.run_until_complete(task)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "/Users/djacobs/.local/share/uv/python/cpython-3.13.2-macos-aarch64-none/lib/python3.13/asyncio/base_events.py", line 725, in run_until_complete
    return future.result()
           ~~~~~~~~~~~~~^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/scripts/demo_seed.py", line 308, in seed
    await conn.run_sync(Base.metadata.create_all)
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/ext/asyncio/engine.py", line 888, in run_sync
    return await greenlet_spawn(
           ^^^^^^^^^^^^^^^^^^^^^
        fn, self._proxied, *arg, _require_await=False, **kw
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 201, in greenlet_spawn
    result = context.throw(*sys.exc_info())
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/sql/schema.py", line 5928, in create_all
    bind._run_ddl_visitor(
    ~~~~~~~~~~~~~~~~~~~~~^
        ddl.SchemaGenerator, self, checkfirst=checkfirst, tables=tables
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 2467, in _run_ddl_visitor
    ).traverse_single(element)
      ~~~~~~~~~~~~~~~^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/sql/visitors.py", line 661, in traverse_single
    return meth(obj, **kw)
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/sql/ddl.py", line 963, in visit_metadata
    [t for t in tables if self._can_create_table(t)]
                          ~~~~~~~~~~~~~~~~~~~~~~^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/sql/ddl.py", line 928, in _can_create_table
    return not self.checkfirst or not self.dialect.has_table(
                                      ~~~~~~~~~~~~~~~~~~~~~~^
        self.connection, table.name, schema=effective_schema
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "<string>", line 2, in has_table
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/engine/reflection.py", line 89, in cache
    return fn(self, con, *args, **kw)
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/dialects/sqlite/base.py", line 2324, in has_table
    info = self._get_table_pragma(
        connection, "table_info", table_name, schema=schema
    )
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/dialects/sqlite/base.py", line 3034, in _get_table_pragma
    cursor = connection.exec_driver_sql(statement)
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1779, in exec_driver_sql
    ret = self._execute_context(
        dialect,
    ...<5 lines>...
        distilled_parameters,
    )
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1846, in _execute_context
    return self._exec_single_context(
           ~~~~~~~~~~~~~~~~~~~~~~~~~^
        dialect, context, statement, parameters
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1986, in _exec_single_context
    self._handle_dbapi_exception(
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~^
        e, str_statement, effective_parameters, cursor, context
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 2363, in _handle_dbapi_exception
    raise sqlalchemy_exception.with_traceback(exc_info[2]) from e
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
    self.dialect.do_execute(
    ~~~~~~~~~~~~~~~~~~~~~~~^
        cursor, str_statement, effective_parameters, context
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
    cursor.execute(statement, parameters)
    ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/dialects/sqlite/aiosqlite.py", line 182, in execute
    self._adapt_connection._handle_exception(error)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/dialects/sqlite/aiosqlite.py", line 342, in _handle_exception
    raise error
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/dialects/sqlite/aiosqlite.py", line 164, in execute
    self.await_(_cursor.execute(operation, parameters))
    ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 132, in await_only
    return current.parent.switch(awaitable)  # type: ignore[no-any-return,attr-defined] # noqa: E501
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 196, in greenlet_spawn
    value = await result
            ^^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/aiosqlite/cursor.py", line 40, in execute
    await self._execute(self._cursor.execute, sql, parameters)
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/aiosqlite/cursor.py", line 32, in _execute
    return await self._conn._execute(fn, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/aiosqlite/core.py", line 160, in _execute
    return await future
           ^^^^^^^^^^^^
  File "/Users/djacobs/Documents/GitHub/Pinwheel/.venv/lib/python3.13/site-packages/aiosqlite/core.py", line 63, in _connection_worker_thread
    result = function()
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) disk I/O error
[SQL: PRAGMA main.table_info("leagues")]
(Background on this error at: https://sqlalche.me/e/20/e3q8)
```
