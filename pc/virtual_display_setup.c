/* One-purpose elevated installer for the pinned, unmodified signed MttVDD.
 * Does not install certificates, change signing policy, or replace GPU drivers. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <setupapi.h>
#include <newdev.h>
#include <shlobj.h>
#include <sddl.h>
#include <bcrypt.h>
#include <wintrust.h>
#include <softpub.h>
#include <stdio.h>
#include <wchar.h>

static const GUID display_class = {
    0x4d36e968, 0xe325, 0x11ce, {0xbf, 0xc1, 0x08, 0x00, 0x2b, 0xe1, 0x03, 0x18}};
static const wchar_t regpath[] = L"SOFTWARE\\MikeTheTech\\VirtualDisplayDriver";
static wchar_t folder[MAX_PATH];
static BOOL verify_only;
static int fail(const wchar_t *stage, DWORD error) {
    wchar_t text[800], reason[400] = {0};
    FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, NULL, error, 0,
                   reason, 400, NULL);
    _snwprintf(text, 800, L"%ls\n\nError 0x%08lx\n%ls", stage, (unsigned long)error, reason);
    if (verify_only)
        fwprintf(stderr, L"%ls\n", text);
    else
        MessageBoxW(NULL, text, L"ND Screen Mirror display setup", MB_OK | MB_ICONERROR);
    return 1;
}
static BOOL administrator(void) {
    SID_IDENTIFIER_AUTHORITY nt = SECURITY_NT_AUTHORITY;
    PSID sid = NULL;
    BOOL member = FALSE;
    if (AllocateAndInitializeSid(&nt, 2, SECURITY_BUILTIN_DOMAIN_RID, DOMAIN_ALIAS_RID_ADMINS, 0, 0,
                                 0, 0, 0, 0, &sid)) {
        CheckTokenMembership(NULL, sid, &member);
        FreeSid(sid);
    }
    return member;
}
static BOOL checksum(const wchar_t *path, const char *expected) {
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE)
        return FALSE;
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    BYTE buffer[16384], digest[32];
    DWORD read;
    BOOL ok = BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0) == 0;
    if (ok)
        ok = BCryptCreateHash(algorithm, &hash, NULL, 0, NULL, 0, 0) == 0;
    while (ok) {
        if (!ReadFile(file, buffer, sizeof(buffer), &read, NULL)) {
            ok = FALSE;
            break;
        }
        if (!read)
            break;
        ok = BCryptHashData(hash, buffer, read, 0) == 0;
    }
    if (ok)
        ok = BCryptFinishHash(hash, digest, sizeof(digest), 0) == 0;
    if (ok) {
        char hex[65];
        for (unsigned i = 0; i < 32; ++i)
            sprintf(hex + i * 2, "%02x", digest[i]);
        ok = strcmp(hex, expected) == 0;
    }
    if (hash)
        BCryptDestroyHash(hash);
    if (algorithm)
        BCryptCloseAlgorithmProvider(algorithm, 0);
    CloseHandle(file);
    return ok;
}
static BOOL trusted(const wchar_t *path) {
    WINTRUST_FILE_INFO file = {0};
    file.cbStruct = sizeof(file);
    file.pcwszFilePath = path;
    WINTRUST_DATA data = {0};
    data.cbStruct = sizeof(data);
    data.dwUIChoice = WTD_UI_NONE;
    data.dwUnionChoice = WTD_CHOICE_FILE;
    data.pFile = &file;
    data.dwStateAction = WTD_STATEACTION_VERIFY;
    data.dwProvFlags = WTD_CACHE_ONLY_URL_RETRIEVAL;
    GUID action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    LONG status = WinVerifyTrust(NULL, &action, &data);
    data.dwStateAction = WTD_STATEACTION_CLOSE;
    WinVerifyTrust(NULL, &action, &data);
    return status == ERROR_SUCCESS;
}
static BOOL secure_directory(const wchar_t *path) {
    PSECURITY_DESCRIPTOR descriptor = NULL;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
            L"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;GRGX;;;BU)", SDDL_REVISION_1, &descriptor,
            NULL))
        return FALSE;
    SECURITY_ATTRIBUTES attributes = {sizeof(attributes), descriptor, FALSE};
    BOOL ok = CreateDirectoryW(path, &attributes) || GetLastError() == ERROR_ALREADY_EXISTS;
    if (ok && (GetFileAttributesW(path) & FILE_ATTRIBUTE_REPARSE_POINT)) {
        SetLastError(ERROR_INVALID_NAME);
        ok = FALSE;
    }
    if (ok)
        ok = SetFileSecurityW(path, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                              descriptor);
    LocalFree(descriptor);
    return ok;
}
static void json_string(FILE *f, const wchar_t *value) {
    char utf8[1024];
    WideCharToMultiByte(CP_UTF8, 0, value, -1, utf8, sizeof(utf8), NULL, NULL);
    fputc('"', f);
    for (char *p = utf8; *p; ++p) {
        if (*p == '\\' || *p == '"')
            fputc('\\', f);
        fputc(*p, f);
    }
    fputc('"', f);
}
static void xml_string(FILE *f, const wchar_t *value) {
    char utf8[1024];
    WideCharToMultiByte(CP_UTF8, 0, value, -1, utf8, sizeof(utf8), NULL, NULL);
    for (char *p = utf8; *p; ++p) {
        if (*p == '&')
            fputs("&amp;", f);
        else if (*p == '<')
            fputs("&lt;", f);
        else if (*p == '>')
            fputs("&gt;", f);
        else
            fputc(*p, f);
    }
}
static BOOL dimensions(const wchar_t *text, unsigned *value) {
    wchar_t *end;
    unsigned long number = wcstoul(text, &end, 10);
    if (!*text || *end || number < 120 || number > 4096)
        return FALSE;
    *value = (unsigned)number;
    return TRUE;
}
static BOOL write_settings(unsigned width, unsigned height) {
    wchar_t temporary[MAX_PATH], destination[MAX_PATH];
    _snwprintf(temporary, MAX_PATH, L"%ls\\vdd_settings.new", folder);
    _snwprintf(destination, MAX_PATH, L"%ls\\vdd_settings.xml", folder);
    unsigned modes[32][2] = {{320, 240}, {480, 360}, {640, 480},
                             {800, 600}, {960, 720}, {1280, 960}},
             count = 6;
    char previous[32769] = {0};
    FILE *old = _wfopen(destination, L"rb");
    if (old) {
        size_t bytes = fread(previous, 1, sizeof(previous) - 1, old);
        fclose(old);
        previous[bytes] = 0;
    }
    for (char *entry = previous; (entry = strstr(entry, "<resolution>")); ++entry) {
        char *finish = strstr(entry, "</resolution>"), *w = strstr(entry, "<width>"),
             *h = strstr(entry, "<height>");
        if (!finish || !w || !h || w > finish || h > finish)
            continue;
        char *we, *he;
        unsigned long x = strtoul(w + 7, &we, 10), y = strtoul(h + 8, &he, 10);
        if (strncmp(we, "</width>", 8) || strncmp(he, "</height>", 9) || x < 120 || y < 120 ||
            x > 4096 || y > 4096)
            continue;
        BOOL duplicate = FALSE;
        for (unsigned i = 0; i < count; ++i)
            if (modes[i][0] == x && modes[i][1] == y)
                duplicate = TRUE;
        if (duplicate || (x == width && y == height))
            continue;
        if (count == 31) {
            memmove(modes + 6, modes + 7, (count - 7) * sizeof(modes[0]));
            --count;
        }
        modes[count][0] = (unsigned)x;
        modes[count++][1] = (unsigned)y;
    }
    BOOL listed = FALSE;
    for (unsigned i = 0; i < count; ++i)
        if (modes[i][0] == width && modes[i][1] == height)
            listed = TRUE;
    if (!listed) {
        modes[count][0] = width;
        modes[count++][1] = height;
    }
    DeleteFileW(temporary);
    FILE *settings = _wfopen(temporary, L"wb");
    if (!settings)
        return FALSE;
    fputs("<?xml version=\"1.0\" "
          "encoding=\"utf-8\"?><vdd_settings><monitors><count>1</count></"
          "monitors><gpu><friendlyname>",
          settings);
    wchar_t gpu[128] = L"default";
    DISPLAY_DEVICEW display = {0};
    display.cb = sizeof(display);
    for (DWORD i = 0; EnumDisplayDevicesW(NULL, i, &display, 0); ++i)
        if (display.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE) {
            wcscpy(gpu, display.DeviceString);
            break;
        }
    xml_string(settings, gpu);
    fputs("</friendlyname></gpu><resolutions>", settings);
    for (unsigned i = 0; i < count; ++i)
        fprintf(settings,
                "<resolution><width>%u</width><height>%u</height><refresh_rate>60</refresh_rate></"
                "resolution>",
                modes[i][0], modes[i][1]);
    fputs("</resolutions><options><CustomEdid>false</CustomEdid><HardwareCursor>true</"
          "HardwareCursor><SDR10bit>false</SDR10bit><HDRPlus>false</HDRPlus><logging>false</"
          "logging><debuglogging>false</debuglogging></options></vdd_settings>\n",
          settings);
    if (fclose(settings)) {
        DeleteFileW(temporary);
        SetLastError(ERROR_WRITE_FAULT);
        return FALSE;
    }
    return MoveFileExW(temporary, destination, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
}
static BOOL restart_owned(HDEVINFO devices, SP_DEVINFO_DATA *device) {
    SP_PROPCHANGE_PARAMS change = {0};
    change.ClassInstallHeader.cbSize = sizeof(SP_CLASSINSTALL_HEADER);
    change.ClassInstallHeader.InstallFunction = DIF_PROPERTYCHANGE;
    change.StateChange = DICS_PROPCHANGE;
    change.Scope = DICS_FLAG_CONFIGSPECIFIC;
    return SetupDiSetClassInstallParamsW(devices, device, &change.ClassInstallHeader,
                                         sizeof(change)) &&
           SetupDiCallClassInstaller(DIF_PROPERTYCHANGE, devices, device);
}
static int update_owned(const wchar_t *marker, unsigned width, unsigned height) {
    char record[2048] = {0}, encoded[1024];
    FILE *file = _wfopen(marker, L"rb");
    if (!file)
        return fail(L"The managed display ownership record could not be read.", ERROR_INVALID_DATA);
    size_t length = fread(record, 1, sizeof(record) - 1, file);
    BOOL complete = feof(file);
    fclose(file);
    if (!complete || !length || !strstr(record, "\"provider\":\"VirtualDrivers/MttVDD\""))
        return fail(L"Invalid managed display ownership record.", ERROR_INVALID_DATA);
    char *id = strstr(record, "\"device_instance\":\"");
    if (!id)
        return fail(L"Missing managed display device.", ERROR_INVALID_DATA);
    id += strlen("\"device_instance\":\"");
    unsigned used = 0;
    while (*id && *id != '"' && used < sizeof(encoded) - 1) {
        if (*id == '\\') {
            ++id;
            if (*id != '\\')
                return fail(L"Invalid managed device identifier.", ERROR_INVALID_DATA);
        }
        encoded[used++] = *id++;
    }
    encoded[used] = 0;
    if (*id != '"')
        return fail(L"Invalid managed device identifier.", ERROR_INVALID_DATA);
    wchar_t instance[1024];
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, encoded, -1, instance, 1024))
        return fail(L"Invalid managed device identifier.", ERROR_INVALID_DATA);
    HDEVINFO devices = SetupDiCreateDeviceInfoList(&display_class, NULL);
    SP_DEVINFO_DATA device = {0};
    device.cbSize = sizeof(device);
    DWORD error = 0;
    if (devices == INVALID_HANDLE_VALUE)
        return fail(L"Could not access the managed display.", GetLastError());
    if (!SetupDiOpenDeviceInfoW(devices, instance, NULL, 0, &device)) {
        error = GetLastError();
        goto finish;
    }
    wchar_t ids[2048] = {0};
    BOOL expected = FALSE;
    if (SetupDiGetDeviceRegistryPropertyW(devices, &device, SPDRP_HARDWAREID, NULL, (BYTE *)ids,
                                          sizeof(ids), NULL))
        for (wchar_t *id = ids; *id; id += wcslen(id) + 1)
            if (!_wcsicmp(id, L"Root\\MttVDD"))
                expected = TRUE;
    if (!expected) {
        error = ERROR_INVALID_DATA;
        goto finish;
    }
    wchar_t settings[MAX_PATH], backup[MAX_PATH];
    _snwprintf(settings, MAX_PATH, L"%ls\\vdd_settings.xml", folder);
    _snwprintf(backup, MAX_PATH, L"%ls\\vdd_settings.previous", folder);
    DeleteFileW(backup);
    if (!CopyFileW(settings, backup, TRUE)) {
        error = GetLastError();
        goto finish;
    }
    if (!write_settings(width, height)) {
        error = GetLastError();
        DeleteFileW(backup);
        goto finish;
    }
    if (!restart_owned(devices, &device)) {
        error = GetLastError();
        MoveFileExW(backup, settings, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
        restart_owned(devices, &device);
        goto finish;
    }
    DeleteFileW(backup);
finish:
    SetupDiDestroyDeviceInfoList(devices);
    return error ? fail(L"Could not update the managed display resolutions.", error) : 0;
}
int wmain(int argc, wchar_t **argv) {
    verify_only = argc == 3 && !wcscmp(argv[1], L"--verify-driver");
    if (argc != 2 && argc != 4 && !verify_only)
        return fail(L"Expected the packaged display driver directory and optional width/height.",
                    ERROR_INVALID_PARAMETER);
    unsigned width = 320, height = 240;
    if (argc == 4 && (!dimensions(argv[2], &width) || !dimensions(argv[3], &height)))
        return fail(L"Display dimensions must be integers between 120 and 4096.",
                    ERROR_INVALID_PARAMETER);
    if (!verify_only && !administrator())
        return fail(L"Windows administrator approval is required to install the display driver.",
                    ERROR_ACCESS_DENIED);
    wchar_t source[MAX_PATH], path[MAX_PATH], parent[MAX_PATH], marker[MAX_PATH];
    DWORD source_length = GetFullPathNameW(argv[verify_only ? 2 : 1], MAX_PATH, source, NULL);
    if (!source_length || source_length + 20 >= MAX_PATH)
        return fail(L"Invalid or excessively long driver directory.", ERROR_FILENAME_EXCED_RANGE);
    const wchar_t *names[] = {L"MttVDD.inf", L"MttVDD.dll", L"mttvdd.cat"};
    const char *hashes[] = {"550d211fe481e74dfe3f9d724ed78be48b3a9113405965d683d9373e8d672f5d",
                            "c9ca837f57a98fbd43bc416a7f535a95843626e7759eaf85cf0cd7ce334dbb05",
                            "08a0093fc9b2e32b287a6f8a77ca4de0a31830d29fc33d2b13a918dc859468f6"};
    for (unsigned i = 0; i < 3; ++i) {
        _snwprintf(path, MAX_PATH, L"%ls\\%ls", source, names[i]);
        if (!checksum(path, hashes[i]))
            return fail(L"The packaged display driver did not match its expected checksum.",
                        ERROR_INVALID_DATA);
        if (i && !trusted(path))
            return fail(L"Windows could not verify the display driver's signature.",
                        TRUST_E_SUBJECT_NOT_TRUSTED);
    }
    if (verify_only) {
        puts("PASS pinned display driver hashes and Windows signature validation; no installation "
             "or display changes.");
        return 0;
    }
    if (FAILED(SHGetFolderPathW(NULL, CSIDL_COMMON_APPDATA, NULL, SHGFP_TYPE_CURRENT, parent)))
        return fail(L"Could not locate ProgramData.", ERROR_PATH_NOT_FOUND);
    wcscat(parent, L"\\ND Screen Mirror");
    _snwprintf(folder, MAX_PATH, L"%ls\\VirtualDisplay", parent);
    _snwprintf(marker, MAX_PATH, L"%ls\\owner.json", folder);
    HKEY key = NULL;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, regpath, 0, KEY_READ, &key) == ERROR_SUCCESS) {
        wchar_t existing[MAX_PATH];
        DWORD bytes = sizeof(existing);
        LONG found = RegQueryValueExW(key, L"VDDPATH", NULL, NULL, (BYTE *)existing, &bytes);
        RegCloseKey(key);
        if (found == ERROR_SUCCESS) {
            if (!_wcsicmp(existing, folder) &&
                GetFileAttributesW(marker) != INVALID_FILE_ATTRIBUTES) {
                if (!secure_directory(parent) || !secure_directory(folder))
                    return fail(L"Could not validate the managed configuration directory.",
                                GetLastError());
                return update_owned(marker, width, height);
            }
            return fail(L"An existing Virtual Display Driver configuration was found. It will not "
                        L"be overwritten.",
                        ERROR_ALREADY_EXISTS);
        }
    }
    HDEVINFO present = SetupDiGetClassDevsW(&display_class, NULL, NULL, DIGCF_PRESENT);
    SP_DEVINFO_DATA check = {0};
    check.cbSize = sizeof(check);
    if (present != INVALID_HANDLE_VALUE) {
        for (DWORD i = 0; SetupDiEnumDeviceInfo(present, i, &check); ++i) {
            wchar_t ids[2048] = {0};
            if (SetupDiGetDeviceRegistryPropertyW(present, &check, SPDRP_HARDWAREID, NULL,
                                                  (BYTE *)ids, sizeof(ids), NULL))
                for (wchar_t *id = ids; *id; id += wcslen(id) + 1)
                    if (!_wcsicmp(id, L"Root\\MttVDD") || !_wcsicmp(id, L"MttVDD")) {
                        SetupDiDestroyDeviceInfoList(present);
                        return fail(L"Another Virtual Display Driver is already installed. It will "
                                    L"not be replaced.",
                                    ERROR_ALREADY_EXISTS);
                    }
        }
        SetupDiDestroyDeviceInfoList(present);
    }
    if (!secure_directory(parent) || !secure_directory(folder))
        return fail(L"Could not create the display configuration directory.", GetLastError());
    for (unsigned i = 0; i < 3; ++i) {
        wchar_t destination[MAX_PATH];
        _snwprintf(path, MAX_PATH, L"%ls\\%ls", source, names[i]);
        _snwprintf(destination, MAX_PATH, L"%ls\\%ls", folder, names[i]);
        DeleteFileW(destination);
        if (!CopyFileW(path, destination, TRUE) || !checksum(destination, hashes[i]))
            return fail(L"Could not copy and verify the signed display driver.", GetLastError());
    }
    if (!write_settings(width, height))
        return fail(L"Could not write display settings.", GetLastError());
    if (RegCreateKeyExW(HKEY_LOCAL_MACHINE, regpath, 0, NULL, 0, KEY_SET_VALUE, NULL, &key, NULL) !=
        ERROR_SUCCESS)
        return fail(L"Could not register the display settings location.", ERROR_ACCESS_DENIED);
    LONG written = RegSetValueExW(key, L"VDDPATH", 0, REG_SZ, (BYTE *)folder,
                                  (DWORD)((wcslen(folder) + 1) * sizeof(wchar_t)));
    RegCloseKey(key);
    if (written)
        return fail(L"Could not register the display settings location.", written);
    HDEVINFO devices = SetupDiCreateDeviceInfoList(&display_class, NULL);
    SP_DEVINFO_DATA device = {0};
    device.cbSize = sizeof(device);
    BOOL registered = FALSE;
    DWORD error = 0;
    if (devices == INVALID_HANDLE_VALUE) {
        error = GetLastError();
        goto rollback;
    }
    if (!SetupDiCreateDeviceInfoW(devices, L"Display", &display_class,
                                  L"ND Screen Mirror virtual display", NULL, DICD_GENERATE_ID,
                                  &device)) {
        error = GetLastError();
        goto rollback;
    }
    const wchar_t hardware[] = L"Root\\MttVDD\0";
    if (!SetupDiSetDeviceRegistryPropertyW(devices, &device, SPDRP_HARDWAREID, (BYTE *)hardware,
                                           sizeof(hardware)) ||
        !SetupDiCallClassInstaller(DIF_REGISTERDEVICE, devices, &device)) {
        error = GetLastError();
        goto rollback;
    }
    registered = TRUE;
    _snwprintf(path, MAX_PATH, L"%ls\\MttVDD.inf", folder);
    BOOL reboot = FALSE;
    if (!UpdateDriverForPlugAndPlayDevicesW(NULL, L"Root\\MttVDD", path, 0, &reboot)) {
        error = GetLastError();
        goto rollback;
    }
    wchar_t instance[512];
    if (!SetupDiGetDeviceInstanceIdW(devices, &device, instance, 512, NULL)) {
        error = GetLastError();
        goto rollback;
    }
    DeleteFileW(marker);
    FILE *owned = _wfopen(marker, L"wb");
    if (!owned) {
        error = ERROR_WRITE_FAULT;
        goto rollback;
    }
    fputs("{\"schema\":1,\"provider\":\"VirtualDrivers/"
          "MttVDD\",\"release\":\"25.7.23\",\"device_instance\":",
          owned);
    json_string(owned, instance);
    fprintf(owned, ",\"reboot_required\":%s}\n", reboot ? "true" : "false");
    if (fclose(owned)) {
        error = ERROR_WRITE_FAULT;
        goto rollback;
    }
    SetupDiDestroyDeviceInfoList(devices);
    return reboot ? 2 : 0;
rollback:
    DeleteFileW(marker);
    if (registered)
        SetupDiCallClassInstaller(DIF_REMOVE, devices, &device);
    if (devices != INVALID_HANDLE_VALUE)
        SetupDiDestroyDeviceInfoList(devices);
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, regpath, 0, KEY_SET_VALUE, &key) == ERROR_SUCCESS) {
        RegDeleteValueW(key, L"VDDPATH");
        RegCloseKey(key);
    }
    return fail(L"The virtual display driver could not be installed. Its new device was removed; "
                L"existing displays were not reconfigured.",
                error);
}
