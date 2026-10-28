import sys

if '--self-test' in sys.argv:
    from PySide6.QtWidgets import QApplication
    from sbharness.__main__ import Window
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    app.processEvents()
    print(f'SB Harness self-test OK: {window.tabs.count()} tabs, {len(window.skills)} skills')
    window.close()
else:
    from sbharness.__main__ import main
    main()
