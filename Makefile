CC      = gcc
CFLAGS  = -Wall -Wextra -O2 $(shell pkg-config --cflags fuse3)
LDFLAGS = $(shell pkg-config --libs fuse3)
TARGET  = mini_unionfs

.PHONY: all clean install-deps test

all: $(TARGET)
	chmod +x test_unionfs.sh

$(TARGET): mini_unionfs.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

test: all
	bash test_unionfs.sh

install-deps:
	sudo apt-get update && sudo apt-get install -y \
	    libfuse3-dev fuse3 python3-tk python3-pip pkg-config

clean:
	rm -f $(TARGET)
	rm -rf unionfs_test_env
