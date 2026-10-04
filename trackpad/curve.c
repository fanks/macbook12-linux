/* MIT license. Local MacBook10,1 / libinput 1.28.1 pointer configuration.
 * No event interception, grabs, input logging, or system-wide preload.
 * Build: gcc-14 -shared -fPIC -O2 -Wall -Wextra -Werror -Wl,-z,relro,-z,now
 *        -Iheaders/usr/include -o libmacbook-trackpad.so this.c -ldl -pthread
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <libinput.h>
#include <math.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <limits.h>

#define NPOINTS 64
#define DPI 2413.0 /* Apple SPI Touchpad ABS_X.resolution = 95 counts/mm */

static pthread_once_t once = PTHREAD_ONCE_INIT;
static _Thread_local bool applying;
/* 1.28.1's custom speed setter does not store the request. Preserve it for
 * this exact built-in trackpad, including reconnects; retain no device pointer.
 * Never hold the cache mutex while calling libinput or reading files.
 */
static pthread_mutex_t speed_mutex = PTHREAD_MUTEX_INITIALIZER;
static bool speed_known;
static double requested_speed;
static bool available;
static struct {
    enum libinput_config_status (*profile)(struct libinput_device *, enum libinput_config_accel_profile);
    enum libinput_config_status (*speed)(struct libinput_device *, double);
    enum libinput_config_accel_profile (*get_profile)(struct libinput_device *);
    double (*get_speed)(struct libinput_device *);
    const char *(*name)(struct libinput_device *);
    unsigned int (*vendor)(struct libinput_device *);
    unsigned int (*product)(struct libinput_device *);
    uint32_t (*profiles)(struct libinput_device *);
    struct libinput_config_accel *(*create)(enum libinput_config_accel_profile);
    void (*destroy)(struct libinput_config_accel *);
    enum libinput_config_status (*points)(struct libinput_config_accel *, enum libinput_config_accel_type, double, size_t, double *);
    enum libinput_config_status (*apply)(struct libinput_device *, struct libinput_config_accel *);
} api;

static void resolve(void)
{
#define GET(field, symbol) *(void **)(&api.field) = dlsym(RTLD_NEXT, #symbol)
    GET(profile, libinput_device_config_accel_set_profile);
    GET(speed, libinput_device_config_accel_set_speed);
    GET(get_profile, libinput_device_config_accel_get_profile);
    GET(get_speed, libinput_device_config_accel_get_speed);
    GET(name, libinput_device_get_name);
    GET(vendor, libinput_device_get_id_vendor);
    GET(product, libinput_device_get_id_product);
    GET(profiles, libinput_device_config_accel_get_profiles);
    GET(create, libinput_config_accel_create);
    GET(destroy, libinput_config_accel_destroy);
    GET(points, libinput_config_accel_set_points);
    GET(apply, libinput_device_config_accel_apply);
#undef GET
    available = api.profile && api.speed && api.get_profile && api.get_speed && api.name && api.vendor && api.product &&
        api.profiles && api.create && api.destroy && api.points && api.apply;
}

static FILE *open_config(bool medium)
{
#if defined(CONFIG) && defined(MEDIUM_CONFIG)
    /* Isolated C/Python agreement tests supply private configuration paths. */
    return fopen(medium ? MEDIUM_CONFIG : CONFIG, "re");
#else
    const char *base = getenv("MACBOOK_TRACKPAD_DIR");
    char path[PATH_MAX];
    if (!base || base[0] != '/') { errno = ENOENT; return NULL; }
    int length = snprintf(path, sizeof path, "%s/%s", base,
                          medium ? "medium.conf" : "curve.conf");
    if (length < 0 || (size_t)length >= sizeof path) { errno = ENAMETOOLONG; return NULL; }
    return fopen(path, "re");
#endif
}

static bool make_curve(double *motion, double *constant, double *slow, double *boost, double *medium)
{
    FILE *f = open_config(false);
    if (!f) return false; /* Removing the file disables the custom curve. */
    char extra;
    int count = fscanf(f, "%lf %lf %c", slow, boost, &extra);
    fclose(f);
    if (count != 2 || !isfinite(*slow) || !isfinite(*boost) ||
        *slow < 0.3 || *slow > 0.9 || *boost < 1.0 || *boost > 4.0)
        return false;

    /* Optional separate file preserves compatibility with the first version. */
    *medium = 1.0;
    f = open_config(true);
    if (f) {
        count = fscanf(f, "%lf %c", medium, &extra);
        fclose(f);
        if (count != 1) return false;
    } else if (errno != ENOENT) return false;
    /* 1.8 keeps output monotonic across the full slow/fast range. */
    if (!isfinite(*medium) || *medium < 1.0 || *medium > 1.8) return false;

    const double base = 0.2968 * 1000.0 / DPI;
    constant[0] = 0;
    constant[1] = 0.9 * base; /* Same flat scroll/fallback gain as adaptive. */
    for (size_t i = 0; i < NPOINTS; i++) {
        double v = i * 10.0; /* Physical finger speed in mm/s. */
        double factor;
        if (v <= 10) factor = *slow;
        else if (v < 30) factor = *slow + (0.9 - *slow) * (v - 10) / 20;
        else {
            double capped = v < 520 ? v : 520;
            factor = capped < 130 ? 0.9 :
                0.0025 * (capped / 130) * (capped - 130) + 0.9;
            double t = (v - 100) / 160;
            if (t < 0) t = 0;
            if (t > 1) t = 1;
            factor *= 1 + (*boost - 1) * t * t * (3 - 2 * t);
        }
        /* More middle travel, with the accepted slow and fast endpoints intact. */
        double weight = 0;
        if (v > 30 && v < 60) {
            double t = (v - 30) / 30;
            weight = t * t * (3 - 2 * t);
        } else if (v >= 60 && v <= 160) {
            weight = 1;
        } else if (v > 160 && v < 260) {
            double t = (v - 160) / 100;
            weight = 1 - t * t * (3 - 2 * t);
        }
        factor *= 1 + (*medium - 1) * weight;
        double input = v * DPI / 25400.0; /* libinput filter counts/ms. */
        motion[i] = input * base * factor; /* API takes output speed. */
        if (!isfinite(motion[i]) || motion[i] < 0 || motion[i] > 10000 ||
            (i && motion[i] < motion[i - 1])) return false;
    }
    /* Last two samples share constant gain: bounded linear extrapolation. */
    return true;
}

static bool target_device(struct libinput_device *device)
{
    if (!available || api.vendor(device) != 0x06cb || api.product(device) != 0x0417)
        return false;
    const char *name = api.name(device);
    return name && !strcmp(name, "Apple SPI Touchpad") &&
        (api.profiles(device) & LIBINPUT_CONFIG_ACCEL_PROFILE_CUSTOM);
}

static double saved_speed(struct libinput_device *device)
{
    double current = api.get_speed(device);
    pthread_mutex_lock(&speed_mutex);
    if (!speed_known) {
        requested_speed = current;
        speed_known = true;
    }
    double saved = requested_speed;
    pthread_mutex_unlock(&speed_mutex);
    return saved;
}

static void apply_curve(struct libinput_device *device)
{
    if (!target_device(device)) return;
    enum libinput_config_accel_profile profile = api.get_profile(device);
    if (profile != LIBINPUT_CONFIG_ACCEL_PROFILE_ADAPTIVE &&
        profile != LIBINPUT_CONFIG_ACCEL_PROFILE_CUSTOM) return;

    /* A removed/invalid config must also turn an existing custom curve off. */
    applying = true;
    const double speed = saved_speed(device);
    if (api.profile(device, LIBINPUT_CONFIG_ACCEL_PROFILE_ADAPTIVE) != LIBINPUT_CONFIG_STATUS_SUCCESS) {
        applying = false;
        return;
    }
    api.speed(device, speed);
    double motion[NPOINTS], constant[2], slow, boost, medium;
    if (!make_curve(motion, constant, &slow, &boost, &medium)) { applying = false; return; }
    struct libinput_config_accel *config = api.create(LIBINPUT_CONFIG_ACCEL_PROFILE_CUSTOM);
    if (!config) { applying = false; return; }
    bool ready = api.points(config, LIBINPUT_ACCEL_TYPE_MOTION,
                            10.0 * DPI / 25400.0, NPOINTS, motion) == LIBINPUT_CONFIG_STATUS_SUCCESS &&
        api.points(config, LIBINPUT_ACCEL_TYPE_SCROLL, 1, 2, constant) == LIBINPUT_CONFIG_STATUS_SUCCESS &&
        api.points(config, LIBINPUT_ACCEL_TYPE_FALLBACK, 1, 2, constant) == LIBINPUT_CONFIG_STATUS_SUCCESS;
    if (ready) {
        /* apply() itself calls the public profile setter; guard reentry. */
        enum libinput_config_status custom_result = api.apply(device, config);
        if (custom_result != LIBINPUT_CONFIG_STATUS_SUCCESS) {
            api.profile(device, LIBINPUT_CONFIG_ACCEL_PROFILE_ADAPTIVE);
            fprintf(stderr, "macbook-trackpad: custom curve rejected; using standard profile\n");
        } else {
            fprintf(stderr, "macbook-trackpad: custom curve applied (slow=%.2f, fast=%.2f, medium=%.2f)\n", slow, boost, medium);
        }
        if (custom_result != LIBINPUT_CONFIG_STATUS_SUCCESS) api.speed(device, speed);
    }
    api.destroy(config);
    applying = false;
}

enum libinput_config_status
libinput_device_config_accel_set_profile(struct libinput_device *device,
                                         enum libinput_config_accel_profile profile)
{
    pthread_once(&once, resolve);
    if (!api.profile) return LIBINPUT_CONFIG_STATUS_UNSUPPORTED;
    bool target = !applying && target_device(device);
    double speed = target ? saved_speed(device) : 0;
    enum libinput_config_status result = api.profile(device, profile);
    if (target && result == LIBINPUT_CONFIG_STATUS_SUCCESS &&
        profile != LIBINPUT_CONFIG_ACCEL_PROFILE_CUSTOM) api.speed(device, speed);
    if (!applying && result == LIBINPUT_CONFIG_STATUS_SUCCESS &&
        profile == LIBINPUT_CONFIG_ACCEL_PROFILE_ADAPTIVE) apply_curve(device);
    return result;
}

enum libinput_config_status
libinput_device_config_accel_set_speed(struct libinput_device *device, double speed)
{
    pthread_once(&once, resolve);
    if (!api.speed) return LIBINPUT_CONFIG_STATUS_UNSUPPORTED;
    enum libinput_config_status result = api.speed(device, speed);
    if (!applying && result == LIBINPUT_CONFIG_STATUS_SUCCESS && target_device(device)) {
        pthread_mutex_lock(&speed_mutex);
        requested_speed = speed;
        speed_known = true;
        pthread_mutex_unlock(&speed_mutex);
    }
    /* Mutter 48.7 sets speed, but omits touchpad profile on initial add. */
    if (!applying && result == LIBINPUT_CONFIG_STATUS_SUCCESS) apply_curve(device);
    return result;
}
