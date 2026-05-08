#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <unistd.h>

static NSString * const ServiceLabel = @"local.codex-moonside-lamp";

@interface CommandResult : NSObject
@property(nonatomic) int status;
@property(nonatomic, copy) NSString *output;
@end

@implementation CommandResult
@end

static CommandResult *RunCommand(NSString *executable, NSArray<NSString *> *arguments, NSString *cwd) {
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:executable];
    task.arguments = arguments;
    if (cwd.length > 0) {
        task.currentDirectoryURL = [NSURL fileURLWithPath:cwd];
    }

    NSPipe *pipe = [NSPipe pipe];
    task.standardOutput = pipe;
    task.standardError = pipe;

    CommandResult *result = [[CommandResult alloc] init];
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        result.status = 1;
        result.output = error.localizedDescription ?: @"Could not start command.";
        return result;
    }

    [task waitUntilExit];
    NSData *data = [[pipe fileHandleForReading] readDataToEndOfFile];
    result.status = task.terminationStatus;
    result.output = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"";
    return result;
}

static NSDictionary *ReadJSONFile(NSString *path) {
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (!data) {
        return nil;
    }
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    return [value isKindOfClass:NSDictionary.class] ? value : nil;
}

static NSString *FindProjectRoot(NSString *startPath) {
    NSFileManager *fileManager = NSFileManager.defaultManager;
    NSURL *current = [NSURL fileURLWithPath:startPath];

    while (current.path.length > 1) {
        NSString *marker = [current.path stringByAppendingPathComponent:@"scripts/install-macos-service"];
        if ([fileManager fileExistsAtPath:marker]) {
            return current.path;
        }
        NSURL *parent = [current URLByDeletingLastPathComponent];
        if ([parent.path isEqualToString:current.path]) {
            break;
        }
        current = parent;
    }

    return nil;
}

@interface ProjectPaths : NSObject
@property(nonatomic, copy, readonly) NSString *root;
@property(nonatomic, copy, readonly) NSString *home;
@property(nonatomic, copy, readonly) NSString *configPath;
@property(nonatomic, copy, readonly) NSString *daemonLogPath;
@property(nonatomic, copy, readonly) NSString *hookLogPath;
@property(nonatomic, copy, readonly) NSString *statePath;
@property(nonatomic, copy, readonly) NSString *hookPath;
@property(nonatomic, copy, readonly) NSString *daemonPath;
@property(nonatomic, copy, readonly) NSString *installServicePath;
@property(nonatomic, copy, readonly) NSString *uninstallServicePath;
@end

@implementation ProjectPaths

- (instancetype)init {
    self = [super init];
    if (!self) {
        return nil;
    }

    _home = NSHomeDirectory();

    NSString *envRoot = NSProcessInfo.processInfo.environment[@"CODEX_MOONSIDE_PROJECT_ROOT"];
    if (envRoot.length > 0) {
        _root = envRoot;
    } else {
        NSURL *resourceURL = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:@"ProjectRoot.txt"];
        NSString *resourceRoot = [[NSString stringWithContentsOfURL:resourceURL encoding:NSUTF8StringEncoding error:nil] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        _root = resourceRoot.length > 0 ? resourceRoot : FindProjectRoot(NSFileManager.defaultManager.currentDirectoryPath);
    }

    if (_root.length == 0) {
        _root = NSFileManager.defaultManager.currentDirectoryPath;
    }

    _configPath = [_home stringByAppendingPathComponent:@".codex-moonside-lamp/config.json"];
    _daemonLogPath = [_home stringByAppendingPathComponent:@".codex-moonside-lamp/daemon.log"];
    _hookLogPath = [_home stringByAppendingPathComponent:@".codex-moonside-lamp/hook.log"];
    _statePath = @"/tmp/codex_moonside_state.json";
    _hookPath = [_root stringByAppendingPathComponent:@".venv/bin/codex-moonside-hook"];
    _daemonPath = [_root stringByAppendingPathComponent:@".venv/bin/codex-moonside-daemon"];
    _installServicePath = [_root stringByAppendingPathComponent:@"scripts/install-macos-service"];
    _uninstallServicePath = [_root stringByAppendingPathComponent:@"scripts/uninstall-macos-service"];

    return self;
}

@end

@interface MenuBarController : NSObject
@property(nonatomic, strong) NSStatusItem *statusItem;
@property(nonatomic, strong) ProjectPaths *paths;
@property(nonatomic, strong) NSTimer *timer;
@end

@implementation MenuBarController

- (instancetype)init {
    self = [super init];
    if (!self) {
        return nil;
    }

    _paths = [[ProjectPaths alloc] init];
    _statusItem = [NSStatusBar.systemStatusBar statusItemWithLength:NSVariableStatusItemLength];
    _statusItem.button.title = @"◐";
    _statusItem.button.toolTip = @"Codex Moonside";
    [self rebuildMenu];
    _timer = [NSTimer scheduledTimerWithTimeInterval:2.0 target:self selector:@selector(rebuildMenu) userInfo:nil repeats:YES];
    return self;
}

- (NSMenuItem *)disabledItem:(NSString *)title {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:nil keyEquivalent:@""];
    item.enabled = NO;
    return item;
}

- (NSMenuItem *)actionItem:(NSString *)title selector:(SEL)selector {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:selector keyEquivalent:@""];
    item.target = self;
    return item;
}

- (NSMenuItem *)submenuItem:(NSString *)title submenu:(NSMenu *)submenu {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:nil keyEquivalent:@""];
    item.submenu = submenu;
    return item;
}

- (NSMenuItem *)stateActionItem:(NSString *)title state:(NSString *)state {
    NSMenuItem *item = [self actionItem:title selector:@selector(setStateFromMenuItem:)];
    item.representedObject = state;
    return item;
}

- (NSDictionary *)config {
    return ReadJSONFile(self.paths.configPath);
}

- (NSDictionary *)state {
    return ReadJSONFile(self.paths.statePath);
}

- (BOOL)isServiceRunning {
    NSString *service = [NSString stringWithFormat:@"gui/%d/%@", getuid(), ServiceLabel];
    CommandResult *result = RunCommand(@"/bin/launchctl", @[@"print", service], nil);
    return result.status == 0 && [result.output containsString:@"state = running"];
}

- (NSString *)lampLabel:(NSDictionary *)config {
    NSString *address = config[@"ble_address"];
    if ([address isKindOfClass:NSString.class] && address.length > 0) {
        return address;
    }
    NSString *name = config[@"lamp_name_contains"];
    if ([name isKindOfClass:NSString.class] && name.length > 0) {
        return name;
    }
    return @"Not configured";
}

- (NSString *)statusIconForState:(NSString *)state serviceRunning:(BOOL)serviceRunning {
    if (!serviceRunning) {
        return @"○";
    }
    if ([state isEqualToString:@"working"]) {
        return @"◐";
    }
    if ([state isEqualToString:@"attention"]) {
        return @"◆";
    }
    if ([state isEqualToString:@"tool_done"]) {
        return @"●";
    }
    if ([state isEqualToString:@"ambient"]) {
        return @"◉";
    }
    if ([state isEqualToString:@"error"]) {
        return @"!";
    }
    return @"◌";
}

- (NSString *)formatAmbientSeconds:(id)value {
    if (![value respondsToSelector:@selector(doubleValue)]) {
        return nil;
    }
    double seconds = [value doubleValue];
    if (seconds >= 60) {
        return [NSString stringWithFormat:@"%d min", (int)(seconds / 60)];
    }
    return [NSString stringWithFormat:@"%d sec", (int)seconds];
}

- (void)rebuildMenu {
    NSDictionary *config = self.config;
    NSDictionary *state = self.state;
    BOOL serviceRunning = self.isServiceRunning;
    NSString *stateName = [state[@"state"] isKindOfClass:NSString.class] ? state[@"state"] : @"Unknown";

    self.statusItem.button.title = [self statusIconForState:stateName serviceRunning:serviceRunning];

    NSMenu *menu = [[NSMenu alloc] init];
    [menu addItem:[self disabledItem:@"Codex Moonside"]];

    NSMenu *statusMenu = [[NSMenu alloc] initWithTitle:@"Status"];
    [statusMenu addItem:[self disabledItem:[NSString stringWithFormat:@"Daemon: %@", serviceRunning ? @"Running" : @"Stopped"]]];
    [statusMenu addItem:[self disabledItem:[NSString stringWithFormat:@"Lamp: %@", [self lampLabel:config]]]];
    [statusMenu addItem:[self disabledItem:[NSString stringWithFormat:@"State: %@", stateName]]];

    NSString *event = state[@"event"];
    if ([event isKindOfClass:NSString.class] && event.length > 0) {
        [statusMenu addItem:[self disabledItem:[NSString stringWithFormat:@"Last event: %@", event]]];
    }

    NSString *ambient = [self formatAmbientSeconds:config[@"ambient_after_idle_seconds"]];
    if (ambient.length > 0) {
        [statusMenu addItem:[self disabledItem:[NSString stringWithFormat:@"Ambient after: %@", ambient]]];
    }
    [menu addItem:[self submenuItem:[NSString stringWithFormat:@"Status: %@", stateName] submenu:statusMenu]];

    [menu addItem:NSMenuItem.separatorItem];
    NSMenu *controlsMenu = [[NSMenu alloc] initWithTitle:@"Controls"];
    [controlsMenu addItem:[self stateActionItem:@"Idle" state:@"idle"]];
    [controlsMenu addItem:[self stateActionItem:@"Working" state:@"working"]];
    [controlsMenu addItem:[self stateActionItem:@"Tool Running" state:@"tool_running"]];
    [controlsMenu addItem:[self stateActionItem:@"Tool Done" state:@"tool_done"]];
    [controlsMenu addItem:[self stateActionItem:@"Attention" state:@"attention"]];
    [controlsMenu addItem:[self stateActionItem:@"Error" state:@"error"]];
    [controlsMenu addItem:[self stateActionItem:@"Ambient" state:@"ambient"]];
    [controlsMenu addItem:[self stateActionItem:@"Off" state:@"off"]];
    [controlsMenu addItem:NSMenuItem.separatorItem];
    [controlsMenu addItem:[self actionItem:@"Scan Lamps..." selector:@selector(scanLamps)]];
    [menu addItem:[self submenuItem:@"Controls" submenu:controlsMenu]];

    [menu addItem:NSMenuItem.separatorItem];
    [menu addItem:[self actionItem:serviceRunning ? @"Restart Daemon" : @"Start Daemon" selector:@selector(restartDaemon)]];
    [menu addItem:[self actionItem:@"Stop Daemon" selector:@selector(stopDaemon)]];

    [menu addItem:NSMenuItem.separatorItem];
    [menu addItem:[self actionItem:@"Open Config" selector:@selector(openConfig)]];
    [menu addItem:[self actionItem:@"Open Daemon Log" selector:@selector(openDaemonLog)]];
    [menu addItem:[self actionItem:@"Open Hook Log" selector:@selector(openHookLog)]];
    [menu addItem:[self actionItem:@"Reveal Project" selector:@selector(revealProject)]];

    [menu addItem:NSMenuItem.separatorItem];
    [menu addItem:[self actionItem:@"Quit" selector:@selector(quit)]];
    self.statusItem.menu = menu;
}

- (void)setStateFromMenuItem:(NSMenuItem *)item {
    NSString *state = [item.representedObject isKindOfClass:NSString.class] ? item.representedObject : nil;
    if (state.length > 0) {
        [self writeState:state];
    }
}

- (void)writeState:(NSString *)state {
    CommandResult *result = RunCommand(self.paths.hookPath, @[@"--state", state], self.paths.root);
    if (result.status != 0) {
        [self showAlert:@"Could not write state" message:result.output];
    }
    [self rebuildMenu];
}

- (void)testAttention {
    [self writeState:@"attention"];
}

- (void)testAmbient {
    [self writeState:@"ambient"];
}

- (void)turnOff {
    [self writeState:@"off"];
}

- (void)scanLamps {
    CommandResult *result = RunCommand(self.paths.daemonPath, @[@"--scan"], self.paths.root);
    [self showAlert:@"BLE Scan" message:result.output.length > 0 ? result.output : @"No output."];
}

- (void)restartDaemon {
    CommandResult *result = RunCommand(self.paths.installServicePath, @[], self.paths.root);
    if (result.status != 0) {
        [self showAlert:@"Could not restart daemon" message:result.output];
    }
    [self rebuildMenu];
}

- (void)stopDaemon {
    CommandResult *result = RunCommand(self.paths.uninstallServicePath, @[], self.paths.root);
    if (result.status != 0) {
        [self showAlert:@"Could not stop daemon" message:result.output];
    }
    [self rebuildMenu];
}

- (void)openPath:(NSString *)path {
    if ([NSFileManager.defaultManager fileExistsAtPath:path]) {
        [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:path]];
    } else {
        [self showAlert:@"File not found" message:path];
    }
}

- (void)openConfig {
    [self openPath:self.paths.configPath];
}

- (void)openDaemonLog {
    [self openPath:self.paths.daemonLogPath];
}

- (void)openHookLog {
    [self openPath:self.paths.hookLogPath];
}

- (void)revealProject {
    [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[[NSURL fileURLWithPath:self.paths.root]]];
}

- (void)quit {
    [NSApplication.sharedApplication terminate:nil];
}

- (void)showAlert:(NSString *)title message:(NSString *)message {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = title;
    alert.informativeText = message ?: @"";
    alert.alertStyle = NSAlertStyleInformational;
    [alert runModal];
}

@end

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        MenuBarController *controller = [[MenuBarController alloc] init];
        (void)controller;
        [app run];
    }
    return 0;
}
