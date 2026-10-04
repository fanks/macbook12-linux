/* Isolated calculation test only: no context, device, events or desktop calls. */
static const char *test_config_path;
static const char *test_medium_path;
#define CONFIG test_config_path
#define MEDIUM_CONFIG test_medium_path
#include "../trackpad/curve.c"

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    test_config_path = argv[1];
    test_medium_path = argv[2];
    double motion[NPOINTS], constant[2], slow, fast, medium;
    if (!make_curve(motion, constant, &slow, &fast, &medium)) {
        puts("{\"accepted\":false}");
        return 0;
    }
    /* This API only allocates and validates a curve object. Never apply it. */
    void *library = dlopen("libinput.so.10", RTLD_NOW | RTLD_LOCAL);
    if (!library) return 3;
    struct libinput_config_accel *(*create)(enum libinput_config_accel_profile);
    void (*destroy)(struct libinput_config_accel *);
    enum libinput_config_status (*points)(struct libinput_config_accel *, enum libinput_config_accel_type, double, size_t, double *);
    *(void **)(&create) = dlsym(library, "libinput_config_accel_create");
    *(void **)(&destroy) = dlsym(library, "libinput_config_accel_destroy");
    *(void **)(&points) = dlsym(library, "libinput_config_accel_set_points");
    if (!create || !destroy || !points) return 4;
    struct libinput_config_accel *config = create(LIBINPUT_CONFIG_ACCEL_PROFILE_CUSTOM);
    if (!config) return 5;
    enum libinput_config_status m = points(config, LIBINPUT_ACCEL_TYPE_MOTION, 10.0 * DPI / 25400.0, NPOINTS, motion);
    enum libinput_config_status s = points(config, LIBINPUT_ACCEL_TYPE_SCROLL, 1, 2, constant);
    enum libinput_config_status f = points(config, LIBINPUT_ACCEL_TYPE_FALLBACK, 1, 2, constant);
    destroy(config);
    dlclose(library);
    printf("{\"accepted\":true,\"slow\":%.17g,\"medium\":%.17g,\"fast\":%.17g,\"api_status\":[%d,%d,%d],\"constant\":[%.17g,%.17g],\"motion\":[", slow, medium, fast, m, s, f, constant[0], constant[1]);
    for (size_t i = 0; i < NPOINTS; i++) printf("%s%.17g", i ? "," : "", motion[i]);
    puts("]}");
    return 0;
}
