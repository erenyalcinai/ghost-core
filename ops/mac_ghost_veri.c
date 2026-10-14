/* ghost-veri: Tam Disk Erişimi verilen TEK ikili. Python okuyucuyu ÇOCUK olarak
   koşturur (exec değil), böylece TCC sorumlusu bu ikili kalır; çocuk izni miras alır. */
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
int main(void) {
    const char *home = getenv("HOME"); if (!home) home = "/Users/sahip";
    char script[1024]; snprintf(script, sizeof script, "%s/.ghost/veri_okuyucu.py", home);
    char *argv[] = {"/usr/bin/python3", script, NULL};
    pid_t pid; int st = 0;
    if (posix_spawn(&pid, argv[0], NULL, NULL, argv, environ) != 0) { perror("spawn"); return 1; }
    if (waitpid(pid, &st, 0) < 0) return 1;
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
