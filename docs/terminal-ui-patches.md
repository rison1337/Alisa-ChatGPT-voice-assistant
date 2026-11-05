# Локальные изменения терминальных библиотек

## prompt_toolkit 3.0.53: Windows input lifecycle

В `alisa_cli/_vendor/prompt_toolkit/input/win32.py` изменён `_Win32Handles.ready`.
Уведомление, уже поставленное в очередь, может выполниться после удаления или
замены регистрации ввода. В исходном обработчике `finally` безусловно запускал
новое ожидание через executor старого event loop. При закрытии диалога это
вызывало `RuntimeError: Executor shutdown has been called`.

Перед вызовом callback и повторной регистрацией проверяется идентичность
remove-event текущей регистрации. Устаревший callback не вызывается; новое
ожидание для него не запускается. После завершения waiter его event закрывает
`ready`; при штатном пробуждении remove-event его закрывает waiter.

Лицензия BSD сохранена. URL и SHA-256 исходного wheel остаются в
`terminal-ui-sources.json`; там же записан SHA-256 изменённого файла.
Строка этого файла в установленном RECORD обновлена.
